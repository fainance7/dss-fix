package postgres

import (
	"context"
	"crypto/sha256"
	"encoding/hex"
	"fmt"
	"strings"

	"github.com/jackc/pgx/v5/pgxpool"
)

// SecurityEvent is one record of the append-only security journal (ТЗ п. 4.8.3).
type SecurityEvent struct {
	Event      string
	RemoteAddr string
	Method     string
	Path       string
	Status     int
	Detail     string
}

// SecurityJournal writes to and verifies the security_journal table. The table
// is append-only (UPDATE/DELETE/TRUNCATE are rejected by triggers) and each row
// carries the SHA-256 of the previous one.
type SecurityJournal struct {
	pool *pgxpool.Pool
}

// NewSecurityJournal returns a journal backed by pool.
func NewSecurityJournal(pool *pgxpool.Pool) *SecurityJournal { return &SecurityJournal{pool: pool} }

// Record appends an event; prev_hash, hash and ts are set by the database.
func (j *SecurityJournal) Record(ctx context.Context, e SecurityEvent) error {
	_, err := j.pool.Exec(ctx,
		`INSERT INTO security_journal (event, remote_addr, method, path, status, detail, prev_hash, hash)
		 VALUES ($1, $2, $3, $4, $5, $6, '', '')`,
		e.Event, e.RemoteAddr, e.Method, e.Path, e.Status, e.Detail)
	return err
}

// Verify recomputes the hash chain independently of the database triggers. It
// returns the number of records checked and the id of the first broken record
// (0 when the chain is intact).
func (j *SecurityJournal) Verify(ctx context.Context) (checked int64, brokenID int64, err error) {
	rows, err := j.pool.Query(ctx,
		`SELECT id, to_char(ts AT TIME ZONE 'UTC', 'YYYY-MM-DD"T"HH24:MI:SS.US'), event, remote_addr, method, path,
		        status, detail, prev_hash, hash
		   FROM security_journal ORDER BY id`)
	if err != nil {
		return 0, 0, err
	}
	defer rows.Close()
	prev := strings.Repeat("0", 64)
	for rows.Next() {
		var (
			id                                    int64
			ts, event, addr, method, path, detail string
			status                                int
			prevHash, hash                        string
		)
		if err := rows.Scan(&id, &ts, &event, &addr, &method, &path, &status, &detail, &prevHash, &hash); err != nil {
			return checked, 0, err
		}
		sum := sha256.Sum256([]byte(fmt.Sprintf("%s|%d|%s|%s|%s|%s|%s|%d|%s",
			prev, id, ts, event, addr, method, path, status, detail)))
		if prevHash != prev || hex.EncodeToString(sum[:]) != hash {
			return checked, id, nil
		}
		prev = hash
		checked++
	}
	return checked, 0, rows.Err()
}
