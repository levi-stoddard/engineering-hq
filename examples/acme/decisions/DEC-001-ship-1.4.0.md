# DEC-001: ship thermostat firmware 1.4.0

Decision: release Acme thermostat firmware 1.4.0 to the update channel.

Evidence cited: the unit-test run recorded for the 1.4.0 build (see the evidence register).

Rollback: if field reports show a regression, roll back to the previous release with a protected
`rollback` request; the supervisor verifies the previous manifest before switching.

Required approvers: owner, reviewer.
