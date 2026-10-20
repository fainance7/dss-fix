-- Migration 005: add 'archived' to development_scenarios status check constraint
-- Fixes: IntegrityError when approving a scenario (sibling scenarios get status='archived')

ALTER TABLE development_scenarios
  DROP CONSTRAINT development_scenarios_status_check;

ALTER TABLE development_scenarios
  ADD CONSTRAINT development_scenarios_status_check
  CHECK (status IN ('draft', 'calculated', 'approved', 'rejected', 'archived'));
