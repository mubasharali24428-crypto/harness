-- 0034: cost_ledger_daily.team_id joins the deletion story. 0027 shipped
-- the rollup table with a plain FK (pg confdeltype 'a' NO ACTION), so a team
-- delete would be blocked by / orphan its daily rollups while every other
-- team_id reference cascades. The M15 D1 deletion map
-- (tests/test_fk_deletion_story.py DELETION_MAP) is the law; align the table.
-- Runner-safe: no dollar quotes, no transaction control, no semicolons in
-- comments.

ALTER TABLE cost_ledger_daily
    DROP CONSTRAINT IF EXISTS cost_ledger_daily_team_id_fkey;

ALTER TABLE cost_ledger_daily
    ADD CONSTRAINT cost_ledger_daily_team_id_fkey
        FOREIGN KEY (team_id) REFERENCES teams (id) ON DELETE CASCADE;
