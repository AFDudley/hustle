//! Rust-side checks for `hustle_core_proto`. These run with `cargo test`
//! inside the container, independent of Charon and Aeneas: they confirm
//! the prototype behaves before it is fed to the translation tools.

use hustle_core_proto::*;

#[test]
fn step_pure_keeps_two_fighters() {
    let state = new_match(42);
    let inputs = Inputs {
        p1: Input { move_x: 1, jump: false, attack: true },
        p2: Input { move_x: -1, jump: true, attack: false },
    };
    let mut s = state;
    for _ in 0..30 {
        s = step_pure(&s, &inputs).expect("no overflow in 30 ticks");
        assert_eq!(fighter_count(&s.objs), 2);
    }
}

#[test]
fn step_pure_and_step_in_place_agree() {
    let inputs = Inputs {
        p1: Input { move_x: 1, jump: false, attack: true },
        p2: Input { move_x: -1, jump: true, attack: false },
    };
    let mut a = new_match(7);
    let mut b = new_match(7);
    for _ in 0..20 {
        a = step_pure(&a, &inputs).expect("pure step");
        step_in_place(&mut b, &inputs).expect("in-place step");
        assert_eq!(a.objs.len(), b.objs.len());
        for i in 0..a.objs.len() {
            assert_eq!(a.objs[i].x.0, b.objs[i].x.0);
            assert_eq!(a.objs[i].y.0, b.objs[i].y.0);
            assert_eq!(a.objs[i].hp, b.objs[i].hp);
        }
    }
}

#[test]
fn aabb_overlap_detects_contact() {
    let a = new_match(1).objs[0];
    let mut b = a;
    b.x = Fixed(a.x.0 + a.w.0 - 1);
    assert!(aabb_overlap(&a, &b).unwrap());
    b.x = Fixed(a.x.0 + a.w.0 + 1);
    assert!(!aabb_overlap(&a, &b).unwrap());
}

#[test]
fn checked_mul_matches_i128_reference() {
    let a = Fixed(3 * SCALE);
    let b = Fixed(-2 * SCALE);
    let r = a.checked_mul(b).unwrap();
    assert_eq!(r.0, -6 * SCALE);
}

#[test]
fn checked_add_is_commutative_when_it_does_not_overflow() {
    let a = Fixed(123456);
    let b = Fixed(-654321);
    assert_eq!(a.checked_add(b), b.checked_add(a));
}
