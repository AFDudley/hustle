-- Hand-written theorem about `HustleCore`, the Charon + Aeneas output.
-- See ../../README.md ("The theorem") for the proof's status and the
-- commands that produced `HustleCore`.
import HustleCore

open hustle_core_proto
open Aeneas Aeneas.Std Result ControlFlow Error

namespace HustleCheck

/-- `Fixed.checked_add` (the generated form of `rust_proto`'s
`Fixed::checked_add`, `src/lib.rs` lines 63-68) is commutative: swapping
the operands gives the same `Result`, whether it succeeds or overflows.
`Fixed` is a reducible alias of `Std.I64` (see `HustleCore/Types.lean`),
and `Fixed.checked_add` is `lift (Std.I64.checked_add self other)`
followed by a `match` that re-wraps `none`/`some` as our own
`core.result.Result.Err`/`Ok` (see `HustleCore/Funs.lean`, `Fixed.checked_add`).
So the whole statement reduces to `Std.I64.checked_add` being
commutative, which in turn reduces to `Int` addition being commutative:
`Std.I64.checked_add a b` is `Option.ofResult (IScalar.tryMk ty (a.val + b.val))`
(through `core.num.checked_add_IScalar`, `Aeneas/Std/Scalar/CheckedOps/Add.lean`),
and `IScalar.tryMk` only looks at its `Int` argument. -/
theorem fixed_checked_add_comm (a b : Fixed) :
    Fixed.checked_add a b = Fixed.checked_add b a := by
  have hab : a + b = b + a := by
    show Aeneas.Std.IScalar.add a b = Aeneas.Std.IScalar.add b a
    simp only [Aeneas.Std.IScalar.add, Int.add_comm a.val b.val]
  simp only [Fixed.checked_add, Std.I64.checked_add,
    Aeneas.Std.core.num.checked_add_IScalar, hab]

/-- Corollary in the shape the lab book's decision 5 asks for: on the
ticks where `Fixed.checked_add` does not overflow, swapping the operands
gives the same sum. -/
theorem fixed_checked_add_comm_on_success (a b v : Fixed) :
    Fixed.checked_add a b = Result.ok (core.result.Result.Ok v) →
    Fixed.checked_add b a = Result.ok (core.result.Result.Ok v) := by
  intro h
  rw [← fixed_checked_add_comm]
  exact h

end HustleCheck
