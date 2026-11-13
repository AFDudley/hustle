# Session notes: Hustle replays, mods, and AI design

Note: this is a notes export, not the raw transcript. The raw chat log is not available to me.

## Goal
- Collect Hustle (github.com/uzkbwza/hustle) replays, strategy docs, and videos from Discord guild 1034428516919418880.
- Check for a botting community.
- Find the source of the AI mods.
- Review how the General AI and Auto Battle mods work.
- Decide between a symbolic approach and a neural network for a button-pushing bot.

## Collected data (local, bitwise3)
- Path: /Users/rix/code/git_puller/repos/dancing_machine/hustle-replays/ (moved from /Users/rix/tmp/hustle-replays, which no longer exists)
- Replays: 1,824 .replay files (109 MB). Godot store_var format. Not parsed.
- PDFs: 46 unique (353 MB). Not opened.
- Videos: 158 unique (2.3 GB), from 5 of 400 reachable search pages.
- dancing_machine: git branch main, no commits, no remote, no .gitignore. hustle-replays/ untracked. Nothing committed or pushed.
- Mirrored to kelce: ~/code/git_puller/repos/dancing_machine/hustle-replays/ (2,032 files, matched local).

## Community evidence
- Web: budok-ai (LLM bridge), Reddit r/YomiHustle single-player AI mods. No online cheating found.
- Discord: most "bot" hits are CowboyBot, Carl-bot, or jokes. Admin comments about bots and updates. Modding guild: an anti-cheat remark and "free cheats" warnings. Unverified.

## Mods (on kelce, Steam library /home/rix/.steam/debian-installation)
- YOMI Hustle installed (appid 2212330, buildid 23959498). Not started.
- Subscribed by user: General AI (3577160476, AxNoodle) and Auto Battle (3756601373, DeadSpider, fork 1.4.4).
- Also present: YOMIH AI (3831860230, origin unknown), Mod Options Menu (2931722541).
- Removed by Steam: Combat AI (3767396225), AI Mod (3112147708).
- Source: no public repo found. The Workshop zips are the only source.

## Code review (verified by reading the GDScript)
- Both mods extend res://game.gd and spawn AIController nodes that listen for player_actionable.
- make_move runs synchronously on the main thread. The name _start_decision_thread is misleading. This causes freezes on Hard.
- General AI: ghost game via fast_copy_to (hand-written state copy, default on) or copy_to. Overrides base fast_start_game with a copy. Bug: P2 style check uses P1's style index. has_projectile_armor assigned twice.
- Auto Battle: two controllers forced to P1/P2, per-player difficulty. Hard: 30-frame fast scan, top 8 rescored up to 70 frames, plus a counter-read pass. Always uses copy_to. Learning via _move_memory. Auto Lock In.
- Version mismatch: metadata 1.4.4, README 1.4.1.
- Both subscribed: the extension chain may spawn both sets of controllers. General's controller only acts if its AI Player is set to P1 or P2 (default Off). Inferred, not tested.

## Search design (what the bots actually do)
- One-ply search against a fixed opponent guess (best move vs Continue). Hard adds a counter-read.
- Each candidate is simulated in a ghost game, stopping at the first actionable frame. Score is a weighted sum of damage, frame advantage, distance, supers, option deltas, combo growth.
- Weakness: a best response to a point prediction is exploitable. A payoff-matrix mixed strategy would fix this.

## AI approach discussion (opinion, not measured)
- Symbolic (search + payoff matrix): days to weeks. Pairwise values already exist in eval_move.
- RNN by imitation from replays: weeks. Needs a replay parser and a state featurizer. Depends on whether replays store full state or only inputs (unknown).
- Self-play RL: months.
- Recurrent history is useful for opponent modeling, not as the core controller.
- Layers of the action model: physical input, interface action (button + DI + feint), legal action (engine mask). Replays may record a different layer than the bot outputs (unknown).

## Corrections
- The claim that the UI input space is smaller than the valid-input space was NOT measured. It remains open.
- No aim test or pixel-projection test was run. A grep for "aim" in the repo and on kelce found nothing.

## Open items
- Provide README text for the tmp path, or confirm it is gone (it moved).
- Decide which other mods to subscribe to: Combat AI, AI Mod, Combat AI Fork Remastered.
- Decide how many videos to crawl.
- Decide whether to check #unstable-replays.
- Decide whether to add a .gitignore to dancing_machine.
- Check what the replay frames contain: full state or only inputs.
- Measure the UI-layer vs engine-layer action counts for one character.
- Remove temp extraction at /tmp/aireview on kelce.
