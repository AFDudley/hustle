//! udp-netsim: a UDP relay that adds latency, jitter and loss.
//!
//! Hustle's direct multiplayer is ENet over UDP (`Network.host_game_direct`,
//! `Network.join_game_direct`). A network test puts this relay between the two
//! game processes: the client joins the relay's listen address, and the relay
//! forwards to the host. Each datagram waits `delay + U(0, jitter)` before the
//! relay sends it on, and the relay drops it with probability `loss`.
//!
//! Each client address gets its own upstream socket, so the host sees one peer
//! per client, as it would on the internet.
//!
//! Without `--reorder`, each direction of each session stays FIFO: a datagram
//! never leaves before an earlier one in the same direction. Internet paths
//! rarely reorder, and ENet copes with it, but it is opt-in here.
//!
//! The random draws come from a seeded SplitMix64 per session and direction.
//! The draws are deterministic for a given packet order. The packet order itself
//! depends on process timing, so a run is not bit-reproducible.
//!
//! See tools/replay_check/README.md, "Network tests".

use std::cmp::Ordering;
use std::collections::{BinaryHeap, HashMap};
use std::net::{SocketAddr, ToSocketAddrs, UdpSocket};
use std::process::exit;
use std::sync::atomic::{AtomicU64, Ordering as AtomicOrdering};
use std::sync::{Arc, Condvar, Mutex};
use std::thread;
use std::time::{Duration, Instant};

const MAX_DATAGRAM: usize = 65_536;

/// Link parameters for one direction.
#[derive(Clone, Copy, Debug)]
struct Link {
    delay: Duration,
    jitter: Duration,
    loss: f64,
}

#[derive(Debug)]
struct Config {
    listen: SocketAddr,
    upstream: SocketAddr,
    up: Link,
    down: Link,
    reorder: bool,
    seed: u64,
    stats_every: Duration,
}

/// SplitMix64. Small, fast, and good enough for delay and loss draws.
struct Rng(u64);

impl Rng {
    fn next_u64(&mut self) -> u64 {
        self.0 = self.0.wrapping_add(0x9E37_79B9_7F4A_7C15);
        let mut z = self.0;
        z = (z ^ (z >> 30)).wrapping_mul(0xBF58_476D_1CE4_E5B9);
        z = (z ^ (z >> 27)).wrapping_mul(0x94D0_49BB_1331_11EB);
        z ^ (z >> 31)
    }

    /// Uniform in [0, 1).
    fn next_f64(&mut self) -> f64 {
        (self.next_u64() >> 11) as f64 / (1u64 << 53) as f64
    }
}

#[derive(Clone, Copy, PartialEq, Eq, Hash, Debug)]
enum Dir {
    Up,
    Down,
}

/// Where a scheduled datagram goes.
enum Target {
    /// Client to host: send on the session's own upstream socket.
    Upstream(Arc<UdpSocket>),
    /// Host to client: send on the listen socket to the client's address.
    Client(SocketAddr),
}

struct Pending {
    due: Instant,
    seq: u64,
    data: Vec<u8>,
    target: Target,
}

impl PartialEq for Pending {
    fn eq(&self, other: &Self) -> bool {
        self.due == other.due && self.seq == other.seq
    }
}
impl Eq for Pending {}
impl PartialOrd for Pending {
    fn partial_cmp(&self, other: &Self) -> Option<Ordering> {
        Some(self.cmp(other))
    }
}
impl Ord for Pending {
    /// Reversed, so `BinaryHeap` (a max-heap) pops the earliest due time.
    /// `seq` breaks ties in arrival order.
    fn cmp(&self, other: &Self) -> Ordering {
        other.due.cmp(&self.due).then(other.seq.cmp(&self.seq))
    }
}

/// The random state and FIFO floor of one direction of one session.
struct Lane {
    rng: Rng,
    last_due: Instant,
}

struct Stats {
    forwarded: [AtomicU64; 2],
    dropped: [AtomicU64; 2],
}

impl Stats {
    fn idx(dir: Dir) -> usize {
        match dir {
            Dir::Up => 0,
            Dir::Down => 1,
        }
    }
}

struct Shared {
    cfg: Config,
    queue: Mutex<BinaryHeap<Pending>>,
    wake: Condvar,
    seq: AtomicU64,
    lanes: Mutex<HashMap<(SocketAddr, Dir), Lane>>,
    stats: Stats,
}

impl Shared {
    /// Draw the fate of one datagram and queue it if it survives.
    fn schedule(&self, client: SocketAddr, dir: Dir, data: Vec<u8>, target: Target) {
        let link = match dir {
            Dir::Up => self.cfg.up,
            Dir::Down => self.cfg.down,
        };
        let now = Instant::now();
        let due = {
            let mut lanes = self.lanes.lock().expect("lanes lock poisoned");
            let lane = lanes.entry((client, dir)).or_insert_with(|| Lane {
                rng: Rng(lane_seed(self.cfg.seed, client, dir)),
                last_due: now,
            });
            if link.loss > 0.0 && lane.rng.next_f64() < link.loss {
                self.stats.dropped[Stats::idx(dir)].fetch_add(1, AtomicOrdering::Relaxed);
                return;
            }
            let jitter = link.jitter.mul_f64(lane.rng.next_f64());
            let mut due = now + link.delay + jitter;
            if !self.cfg.reorder && due < lane.last_due {
                due = lane.last_due;
            }
            lane.last_due = due;
            due
        };
        let seq = self.seq.fetch_add(1, AtomicOrdering::Relaxed);
        self.queue
            .lock()
            .expect("queue lock poisoned")
            .push(Pending {
                due,
                seq,
                data,
                target,
            });
        self.wake.notify_one();
        self.stats.forwarded[Stats::idx(dir)].fetch_add(1, AtomicOrdering::Relaxed);
    }
}

/// A seed per session and direction, so two clients do not share a stream.
fn lane_seed(seed: u64, client: SocketAddr, dir: Dir) -> u64 {
    let mut h = Rng(seed ^ if dir == Dir::Up { 0x5550 } else { 0x444F_574E });
    let text = client.to_string();
    let mut acc = h.next_u64();
    for b in text.bytes() {
        acc = Rng(acc ^ u64::from(b)).next_u64();
    }
    acc
}

/// Sends each queued datagram at its due time.
fn sender(shared: Arc<Shared>, listen: Arc<UdpSocket>) {
    let mut queue = shared.queue.lock().expect("queue lock poisoned");
    loop {
        let now = Instant::now();
        match queue.peek() {
            None => {
                queue = shared.wake.wait(queue).expect("queue lock poisoned");
            }
            Some(top) if top.due > now => {
                let wait = top.due - now;
                queue = shared
                    .wake
                    .wait_timeout(queue, wait)
                    .expect("queue lock poisoned")
                    .0;
            }
            Some(_) => {
                let p = queue.pop().expect("peeked entry vanished");
                drop(queue);
                let sent = match &p.target {
                    Target::Upstream(sock) => sock.send(&p.data),
                    Target::Client(addr) => listen.send_to(&p.data, addr),
                };
                // A send error is a test-environment fault (a peer exited, a
                // container network went away). Fail loudly.
                if let Err(e) = sent {
                    eprintln!("udp-netsim: send failed: {e}");
                    exit(1);
                }
                queue = shared.queue.lock().expect("queue lock poisoned");
            }
        }
    }
}

/// Reads host replies for one session and queues them to the client.
fn upstream_reader(shared: Arc<Shared>, sock: Arc<UdpSocket>, client: SocketAddr) {
    let mut buf = vec![0u8; MAX_DATAGRAM];
    loop {
        match sock.recv(&mut buf) {
            Ok(n) => shared.schedule(client, Dir::Down, buf[..n].to_vec(), Target::Client(client)),
            Err(e) => {
                eprintln!("udp-netsim: upstream recv for {client} failed: {e}");
                exit(1);
            }
        }
    }
}

fn stats_printer(shared: Arc<Shared>) {
    loop {
        thread::sleep(shared.cfg.stats_every);
        let s = &shared.stats;
        eprintln!(
            "udp-netsim: up forwarded={} dropped={} | down forwarded={} dropped={}",
            s.forwarded[0].load(AtomicOrdering::Relaxed),
            s.dropped[0].load(AtomicOrdering::Relaxed),
            s.forwarded[1].load(AtomicOrdering::Relaxed),
            s.dropped[1].load(AtomicOrdering::Relaxed),
        );
    }
}

const USAGE: &str = "\
usage: udp-netsim --listen ADDR:PORT --upstream HOST:PORT [options]

  --delay-ms N         one-way delay in both directions (default 0)
  --jitter-ms N        extra delay, uniform in [0, N] (default 0)
  --loss P             drop probability per datagram, 0 <= P < 1 (default 0)
  --up-delay-ms N, --up-jitter-ms N, --up-loss P
  --down-delay-ms N, --down-jitter-ms N, --down-loss P
                       per-direction overrides; up = client to host
  --reorder            let jitter reorder datagrams (default: FIFO per direction)
  --seed N             random seed (default 1)
  --stats-secs N       print counters every N seconds (default 5)";

fn fail(msg: &str) -> ! {
    eprintln!("udp-netsim: {msg}\n\n{USAGE}");
    exit(2);
}

fn parse_args() -> Config {
    let args: Vec<String> = std::env::args().skip(1).collect();
    let mut listen = None;
    let mut upstream = None;
    let mut both = Link {
        delay: Duration::ZERO,
        jitter: Duration::ZERO,
        loss: 0.0,
    };
    let (mut up_d, mut up_j, mut up_l) = (None, None, None);
    let (mut dn_d, mut dn_j, mut dn_l) = (None, None, None);
    let mut reorder = false;
    let mut seed = 1u64;
    let mut stats_secs = 5u64;

    let ms = |v: &str, name: &str| -> Duration {
        let n: f64 = v
            .parse()
            .unwrap_or_else(|_| fail(&format!("{name}: not a number: {v}")));
        if !(n >= 0.0 && n.is_finite()) {
            fail(&format!("{name}: must be >= 0"));
        }
        Duration::from_secs_f64(n / 1000.0)
    };
    let prob = |v: &str, name: &str| -> f64 {
        let p: f64 = v
            .parse()
            .unwrap_or_else(|_| fail(&format!("{name}: not a number: {v}")));
        if !(0.0..1.0).contains(&p) {
            fail(&format!("{name}: must be in [0, 1)"));
        }
        p
    };
    let resolve = |v: &str, name: &str| -> SocketAddr {
        v.to_socket_addrs()
            .unwrap_or_else(|e| fail(&format!("{name}: cannot resolve {v}: {e}")))
            .next()
            .unwrap_or_else(|| fail(&format!("{name}: {v} resolved to nothing")))
    };

    let mut i = 0;
    while i < args.len() {
        let flag = args[i].as_str();
        if flag == "--reorder" {
            reorder = true;
            i += 1;
            continue;
        }
        if flag == "-h" || flag == "--help" {
            println!("{USAGE}");
            exit(0);
        }
        let v = args
            .get(i + 1)
            .unwrap_or_else(|| fail(&format!("{flag} needs a value")))
            .as_str();
        match flag {
            "--listen" => listen = Some(resolve(v, flag)),
            "--upstream" => upstream = Some(resolve(v, flag)),
            "--delay-ms" => both.delay = ms(v, flag),
            "--jitter-ms" => both.jitter = ms(v, flag),
            "--loss" => both.loss = prob(v, flag),
            "--up-delay-ms" => up_d = Some(ms(v, flag)),
            "--up-jitter-ms" => up_j = Some(ms(v, flag)),
            "--up-loss" => up_l = Some(prob(v, flag)),
            "--down-delay-ms" => dn_d = Some(ms(v, flag)),
            "--down-jitter-ms" => dn_j = Some(ms(v, flag)),
            "--down-loss" => dn_l = Some(prob(v, flag)),
            "--seed" => {
                seed = v
                    .parse()
                    .unwrap_or_else(|_| fail(&format!("--seed: not an integer: {v}")))
            }
            "--stats-secs" => {
                stats_secs = v
                    .parse()
                    .unwrap_or_else(|_| fail(&format!("--stats-secs: not an integer: {v}")));
                if stats_secs == 0 {
                    fail("--stats-secs: must be > 0");
                }
            }
            _ => fail(&format!("unknown option {flag}")),
        }
        i += 2;
    }

    Config {
        listen: listen.unwrap_or_else(|| fail("--listen is required")),
        upstream: upstream.unwrap_or_else(|| fail("--upstream is required")),
        up: Link {
            delay: up_d.unwrap_or(both.delay),
            jitter: up_j.unwrap_or(both.jitter),
            loss: up_l.unwrap_or(both.loss),
        },
        down: Link {
            delay: dn_d.unwrap_or(both.delay),
            jitter: dn_j.unwrap_or(both.jitter),
            loss: dn_l.unwrap_or(both.loss),
        },
        reorder,
        seed,
        stats_every: Duration::from_secs(stats_secs),
    }
}

fn main() {
    let cfg = parse_args();
    let listen = Arc::new(
        UdpSocket::bind(cfg.listen)
            .unwrap_or_else(|e| fail(&format!("cannot bind {}: {e}", cfg.listen))),
    );
    eprintln!(
        "udp-netsim: listening on {} -> {} | up {:?} | down {:?} | reorder={} seed={}",
        cfg.listen, cfg.upstream, cfg.up, cfg.down, cfg.reorder, cfg.seed
    );

    let shared = Arc::new(Shared {
        cfg,
        queue: Mutex::new(BinaryHeap::new()),
        wake: Condvar::new(),
        seq: AtomicU64::new(0),
        lanes: Mutex::new(HashMap::new()),
        stats: Stats {
            forwarded: [AtomicU64::new(0), AtomicU64::new(0)],
            dropped: [AtomicU64::new(0), AtomicU64::new(0)],
        },
    });

    {
        let (s, l) = (shared.clone(), listen.clone());
        thread::spawn(move || sender(s, l));
    }
    {
        let s = shared.clone();
        thread::spawn(move || stats_printer(s));
    }

    // One upstream socket per client address.
    let mut sessions: HashMap<SocketAddr, Arc<UdpSocket>> = HashMap::new();
    let bind_any: SocketAddr = if shared.cfg.upstream.is_ipv4() {
        "0.0.0.0:0".parse().expect("literal address")
    } else {
        "[::]:0".parse().expect("literal address")
    };
    let mut buf = vec![0u8; MAX_DATAGRAM];
    loop {
        let (n, client) = match listen.recv_from(&mut buf) {
            Ok(r) => r,
            Err(e) => {
                eprintln!("udp-netsim: listen recv failed: {e}");
                exit(1);
            }
        };
        let sock = sessions
            .entry(client)
            .or_insert_with(|| {
                let sock = UdpSocket::bind(bind_any)
                    .and_then(|s| s.connect(shared.cfg.upstream).map(|()| s))
                    .unwrap_or_else(|e| {
                        eprintln!("udp-netsim: cannot open upstream socket for {client}: {e}");
                        exit(1);
                    });
                let sock = Arc::new(sock);
                let (s, u) = (shared.clone(), sock.clone());
                thread::spawn(move || upstream_reader(s, u, client));
                eprintln!("udp-netsim: new session {client}");
                sock
            })
            .clone();
        shared.schedule(client, Dir::Up, buf[..n].to_vec(), Target::Upstream(sock));
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn heap_pops_earliest_due_then_arrival_order() {
        let t0 = Instant::now();
        let mk = |ms: u64, seq: u64| Pending {
            due: t0 + Duration::from_millis(ms),
            seq,
            data: vec![],
            target: Target::Client("127.0.0.1:1".parse().unwrap()),
        };
        let mut h = BinaryHeap::new();
        h.push(mk(30, 0));
        h.push(mk(10, 1));
        h.push(mk(10, 2));
        let order: Vec<u64> = std::iter::from_fn(|| h.pop().map(|p| p.seq)).collect();
        assert_eq!(order, vec![1, 2, 0]);
    }

    #[test]
    fn rng_is_seeded_and_uniform_enough() {
        let mut a = Rng(7);
        let mut b = Rng(7);
        let xs: Vec<f64> = (0..10_000).map(|_| a.next_f64()).collect();
        assert!(xs
            .iter()
            .zip((0..10_000).map(|_| b.next_f64()))
            .all(|(x, y)| *x == y));
        let mean = xs.iter().sum::<f64>() / xs.len() as f64;
        assert!((mean - 0.5).abs() < 0.02, "mean {mean}");
        assert!(xs.iter().all(|x| (0.0..1.0).contains(x)));
    }
}
