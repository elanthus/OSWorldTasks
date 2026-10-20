# Private Git LFS recovery observations

Generated from stored receipts and checked against the published validation bundles.

| Receipt | Pinned remote commit | Downloaded bytes | Scripted cases | Exit status |
| --- | --- | ---: | ---: | ---: |
| `receipt.json` | `626a916acd05bdb90fdd65fc8cefcfffc6e62805` | 2748479 | 7 | 0 |

Unique recorded LFS objects: 1; total packed bytes: 2748479.

Each receipt records a fresh Git/LFS cache, archive-byte verification, and the isolated retained-source check. Its replay and report digests match the original public bundle. No experiment model calls were made.

The GitHub $0 limit with stop usage was observed in the owner billing UI. The local observation does not configure or continuously verify that setting.

These offline checks verify published bindings only. They cannot inspect private remote archive bytes or establish continuous availability. An authorized operator must run the restore command to repeat recovery. This evidence does not restore D5.9 or declare a milestone verdict.
