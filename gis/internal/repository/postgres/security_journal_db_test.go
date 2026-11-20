package postgres

import (
	"context"
	"testing"
)

// ТЗ п. 4.8.3: the security journal is append-only and tampering is detected.
func TestSecurityJournal_AppendOnlyAndVerify(t *testing.T) {
	pool := testPool(t)
	ctx := context.Background()
	j := NewSecurityJournal(pool)
	for _, ev := range []string{"api_key_rejected", "data_change", "read_only_key_write_denied"} {
		if err := j.Record(ctx, SecurityEvent{Event: ev, RemoteAddr: "10.0.0.1:1", Method: "POST", Path: "/datasets", Status: 401}); err != nil {
			t.Fatalf("record: %v", err)
		}
	}
	if _, broken, err := j.Verify(ctx); err != nil || broken != 0 {
		t.Fatalf("verify intact chain: broken=%d err=%v", broken, err)
	}
	for _, stmt := range []string{
		`UPDATE security_journal SET event = 'x'`,
		`DELETE FROM security_journal`,
		`TRUNCATE security_journal`,
	} {
		if _, err := pool.Exec(ctx, stmt); err == nil {
			t.Fatalf("%q succeeded on an append-only journal", stmt)
		}
	}
	// Simulate an administrator bypassing the triggers: verify must notice.
	if _, err := pool.Exec(ctx, `ALTER TABLE security_journal DISABLE TRIGGER security_journal_no_update`); err != nil {
		t.Fatal(err)
	}
	if _, err := pool.Exec(ctx, `UPDATE security_journal SET path = '/forged' WHERE id = (SELECT min(id) FROM security_journal)`); err != nil {
		t.Fatal(err)
	}
	_, _ = pool.Exec(ctx, `ALTER TABLE security_journal ENABLE TRIGGER security_journal_no_update`)
	if _, broken, err := j.Verify(ctx); err != nil || broken == 0 {
		t.Fatalf("tampering not detected: broken=%d err=%v", broken, err)
	}
}
