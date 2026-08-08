# Official Challenge Pack — NOT COMMITTED

Drop the organizer-supplied pack here. **Its contents are gitignored on purpose.**

The brief states: *"Do not upload confidential case materials to unapproved
external services."* A hosted git remote is one. Committing the pack — even to a
private repository — puts us on the wrong side of that rule and of the pack's
licence terms.

## When the pack arrives

```bash
# 1. extract into this directory
# 2. record its identity (this is what git tracks, instead of the data)
python scripts/checksum_pack.py --pack packs/official
# 3. verify the checksums against the organizer download page
# 4. commit the updated docs/data-manifest.md
```

The manifest proves exactly which data version produced our numbers without
redistributing a single byte of it.
