# Legacy supporting-artifact discrepancy

The frozen D4.12 manifest records `artifacts/platform/known-limitations.md` as 1,440 bytes with SHA-256 `1698fa65daf5e462486ab1467bb4ade2cbfe8600b66e7af78717dd39a074aa51`.

At the recorded evidence revision `f92e307af7a3830347d50ca63f6a7d481489935c`, that file is 1,403 bytes with SHA-256 `f9ec2d2c324f1cf90d9134bfd4fbea544ae8008e8ff6777b8553d31c81c6bbdc`. The manifest-recorded 1,440-byte content first appears in commit `a5af1207714926cd11e498e8d26cd3fbf2f9a8e9`.

The manifest is deliberately left unmodified under the owner's decision that frozen evidence is never rewritten.
