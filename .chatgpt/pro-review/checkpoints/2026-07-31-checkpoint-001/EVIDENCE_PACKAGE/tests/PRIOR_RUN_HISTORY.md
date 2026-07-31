# Checkpoint 001 Test History

The checkpoint request disclosed all known provider-free verification attempts:

1. An initial repository-wide run reached 575 tests and produced one error in
   the repository source-inventory validation. The tightened root `/runtime/`
   rule conflicted with a redundant `!src/cera/runtime/**` re-inclusion rule.
2. Two attempted suite invocations used harness timeouts that were too short to
   cover the established repository-wide runtime. They were infrastructure
   timeouts, not test assertions and not provider failures.
3. The source-inventory rule was corrected within authorized Progression 3.
4. The complete provider-free suite then passed 575 of 575 tests in 268.827
   seconds, with no live provider call.

The new logs in this evidence export are a fresh zero-provider verification of
the frozen checkpoint, not a story qualification and not a rerun of historical
live evidence. Test skips, failures, or errors are preserved verbatim in the
captured output.

## Evidence-export attempt 1

The first fresh export run executed 575 tests in 329.167 seconds and reported
one documentation failure. The synchronized conversation export contained a
chat-only `sandbox:/mnt/data/...` download link. CERA's documentation validator
correctly rejected that link because it cannot resolve inside the repository.

The operational `PRO_RESPONSE.md` was normalized by removing only that download
link. The substantive response and its declared original-artifact hash remain
present, while `CODEX_ASSESSMENT.md` retains the attachment provenance. The
focused documentation suite then passed 3/3. The failed run is preserved as
`full_suite_attempt_1.stderr.log`; a fresh complete run follows with no provider
call and no runtime implementation change.
