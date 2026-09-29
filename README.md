# Nitro Toolkit — development moved to Maple

Nitro Toolkit is maintained in the [Maple monorepo](https://github.com/MaplePrivacyLabs/Maple/tree/master/services/opensecret/nitro-toolkit) under `services/opensecret/nitro-toolkit/`. Use that directory for current source, documentation, tests, and new pull requests.

This standalone repository is archived as a historical reference. Its source, Git history, and [MIT license](LICENSE) are retained. The toolkit was imported with its history in [Maple #984](https://github.com/MaplePrivacyLabs/Maple/pull/984); it is ordinary source in Maple, not a submodule.

## Retained work

- [PR #13](https://github.com/OpenSecretCloud/nitro-toolkit/pull/13) remains an unmerged historical proposal. Its byte-preservation fix was implemented separately in [Maple #988](https://github.com/MaplePrivacyLabs/Maple/pull/988). The broader half-close, paired-worker cancellation, cleanup, and logging changes were not included in that replacement; reassess those against current Maple before adopting them.
- [Issue #1](https://github.com/OpenSecretCloud/nitro-toolkit/issues/1) remains historical discussion of socket cleanup and connection management. Archiving this repository does not resolve that issue or approve its proposed idle timeout.

Please open any follow-up work in [Maple](https://github.com/MaplePrivacyLabs/Maple/issues), linking the relevant historical discussion. Existing standalone files are retained for compatibility and are no longer maintained here.
