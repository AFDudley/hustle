-- Root module for the Charon + Aeneas output. Imports the split files
-- Aeneas produced with `-split-files` (see ../README.md for the exact
-- command). Hand-written theorems live in HustleCheck.lean, not here, so
-- a re-run of the pipeline never overwrites them.
import HustleCore.TypesExternal
import HustleCore.Types
import HustleCore.Funs
