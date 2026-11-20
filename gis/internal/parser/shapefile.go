package parser

import (
	"archive/zip"
	"bytes"
	"encoding/binary"
	"errors"
	"fmt"
	"io"
	"strings"
	"unicode/utf8"

	"gis/internal/domain"

	"golang.org/x/text/encoding/charmap"
)

// MaxDBFBytes caps the decompressed size of a shapefile's .dbf (Этап 7).
const MaxDBFBytes = 128 << 20

// readDBF extracts the .dbf bytes from a zipped ESRI shapefile.
func readDBF(data []byte) ([]byte, error) {
	zr, err := zip.NewReader(bytes.NewReader(data), int64(len(data)))
	if err != nil {
		return nil, fmt.Errorf("open zip: %w", err)
	}

	var dbf *zip.File
	for _, f := range zr.File {
		if strings.HasSuffix(strings.ToLower(f.Name), ".dbf") {
			dbf = f
			break
		}
	}
	if dbf == nil {
		return nil, errors.New("no .dbf file found in archive")
	}

	// Этап 7 (ПТБ-17): bound the decompressed size — a few-MB archive can
	// declare (or actually inflate to) gigabytes and exhaust memory.
	if dbf.UncompressedSize64 > MaxDBFBytes {
		return nil, fmt.Errorf(".dbf is too large: %d bytes (limit %d)", dbf.UncompressedSize64, MaxDBFBytes)
	}
	rc, err := dbf.Open()
	if err != nil {
		return nil, fmt.Errorf("open .dbf: %w", err)
	}
	defer rc.Close()

	raw, err := io.ReadAll(io.LimitReader(rc, MaxDBFBytes+1))
	if err != nil {
		return nil, fmt.Errorf("read .dbf: %w", err)
	}
	if int64(len(raw)) > MaxDBFBytes {
		return nil, fmt.Errorf(".dbf is too large (limit %d bytes)", MaxDBFBytes)
	}
	return raw, nil
}

// shapefileColumns reads the .dbf attribute columns (with samples).
func shapefileColumns(data []byte) ([]domain.AttributeColumn, error) {
	raw, err := readDBF(data)
	if err != nil {
		return nil, err
	}

	fields, headerSize, recordLen, err := dbfHeader(raw)
	if err != nil {
		return nil, err
	}

	samples := make([][]string, len(fields))
	collected := 0
	dbfEachRecord(raw, fields, headerSize, recordLen, func(values []string) bool {
		for i := range fields {
			samples[i] = append(samples[i], values[i])
		}
		collected++
		return collected < sampleRows
	})

	cols := make([]domain.AttributeColumn, len(fields))
	for i, f := range fields {
		cols[i] = domain.AttributeColumn{Name: f.name, Samples: samples[i]}
	}
	return cols, nil
}

// shapefileRows reads every record of the .dbf as a name->value map.
func shapefileRows(data []byte) ([]map[string]string, error) {
	raw, err := readDBF(data)
	if err != nil {
		return nil, err
	}

	fields, headerSize, recordLen, err := dbfHeader(raw)
	if err != nil {
		return nil, err
	}

	var rows []map[string]string
	dbfEachRecord(raw, fields, headerSize, recordLen, func(values []string) bool {
		row := make(map[string]string, len(fields))
		for i, f := range fields {
			row[f.name] = values[i]
		}
		rows = append(rows, row)
		return true
	})
	return rows, nil
}

type dbfField struct {
	name   string
	offset int
	length int
}

// dbfHeader parses a dBASE III/IV header into fields plus record geometry.
func dbfHeader(b []byte) (fields []dbfField, headerSize, recordLen int, err error) {
	if len(b) < 32 {
		return nil, 0, 0, errors.New("dbf too short")
	}
	headerSize = int(binary.LittleEndian.Uint16(b[8:10]))
	recordLen = int(binary.LittleEndian.Uint16(b[10:12]))

	recOffset := 1 // first byte of each record is the deletion flag
	for off := 32; off+32 <= len(b) && b[off] != 0x0D; off += 32 {
		name := decodeText(trimNull(b[off : off+11]))
		length := int(b[off+16])
		fields = append(fields, dbfField{name: name, offset: recOffset, length: length})
		recOffset += length
	}
	if len(fields) == 0 {
		return nil, 0, 0, ErrNoColumns
	}
	return fields, headerSize, recordLen, nil
}

// dbfEachRecord decodes each non-deleted record's field values (in field order)
// and calls fn; iteration stops when fn returns false.
func dbfEachRecord(b []byte, fields []dbfField, headerSize, recordLen int, fn func(values []string) bool) {
	if headerSize <= 0 || recordLen <= 0 {
		return
	}
	for start := headerSize; start+recordLen <= len(b); start += recordLen {
		rec := b[start : start+recordLen]
		if rec[0] == '*' { // deleted record
			continue
		}
		values := make([]string, len(fields))
		for i, f := range fields {
			if f.offset+f.length <= len(rec) {
				values[i] = strings.TrimSpace(decodeText(rec[f.offset : f.offset+f.length]))
			}
		}
		if !fn(values) {
			return
		}
	}
}

func trimNull(b []byte) []byte {
	if i := bytes.IndexByte(b, 0); i >= 0 {
		return b[:i]
	}
	return b
}

// decodeText returns UTF-8 text, falling back to Windows-1251 (common for
// Cyrillic KATO data) when the bytes are not valid UTF-8.
func decodeText(b []byte) string {
	if utf8.Valid(b) {
		return string(b)
	}
	if decoded, err := charmap.Windows1251.NewDecoder().Bytes(b); err == nil {
		return string(decoded)
	}
	return string(b)
}
