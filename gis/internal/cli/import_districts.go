package cli

import (
	"encoding/json"
	"fmt"
	"os"

	"gis/internal/config"
	"gis/internal/repository/postgres"

	"github.com/spf13/cobra"
)

// districtFeature is the subset of a GeoJSON feature we care about: the raw
// geometry (handed to PostGIS verbatim) and the kato/name_ru properties. kato
// arrives as a JSON number but is stored as text, so it is decoded as
// json.Number to preserve its exact digits.
type districtFeature struct {
	Geometry json.RawMessage `json:"geometry"`
	Props    struct {
		Kato   json.Number `json:"kato"`
		NameRU string      `json:"name_ru"`
	} `json:"properties"`
}

type districtCollection struct {
	Features []districtFeature `json:"features"`
}

var importDistrictsCmd = &cobra.Command{
	Use:   "import-districts <geojson-file>",
	Short: "Import district boundaries from a GeoJSON file",
	Long: "Parse a districts GeoJSON FeatureCollection and upsert each feature\n" +
		"into the districts table, keyed by KATO code. Each feature's name_ru\n" +
		"and kato properties become name/kato; its geometry is stored as a\n" +
		"MultiPolygon in EPSG:4326. Re-running upserts on kato, so existing\n" +
		"districts have their name and geometry refreshed.\n\n" +
		"Example:\n" +
		"  gis import-districts districts.geojson",
	Args: cobra.ExactArgs(1),
	RunE: func(cmd *cobra.Command, args []string) error {
		raw, err := os.ReadFile(args[0])
		if err != nil {
			return fmt.Errorf("read geojson %q: %w", args[0], err)
		}

		var fc districtCollection
		if err := json.Unmarshal(raw, &fc); err != nil {
			return fmt.Errorf("parse geojson %q: %w", args[0], err)
		}
		if len(fc.Features) == 0 {
			return fmt.Errorf("no features found in %q", args[0])
		}

		ctx, cancel := signalContext()
		defer cancel()

		cfg, err := config.Load()
		if err != nil {
			return err
		}

		pool, err := postgres.Connect(ctx, cfg.DB.URL)
		if err != nil {
			return fmt.Errorf("connect postgres: %w", err)
		}
		defer pool.Close()

		const upsert = `
			INSERT INTO districts (kato, name, coordinates)
			VALUES ($1, $2, ST_Multi(ST_SetSRID(ST_GeomFromGeoJSON($3), 4326)))
			ON CONFLICT (kato) DO UPDATE
			SET name = EXCLUDED.name, coordinates = EXCLUDED.coordinates`

		var imported int
		for i, f := range fc.Features {
			kato := f.Props.Kato.String()
			if kato == "" {
				return fmt.Errorf("feature %d: missing kato", i)
			}
			if f.Props.NameRU == "" {
				return fmt.Errorf("feature %d (kato %s): missing name_ru", i, kato)
			}
			if len(f.Geometry) == 0 {
				return fmt.Errorf("feature %d (kato %s): missing geometry", i, kato)
			}

			if _, err := pool.Exec(ctx, upsert, kato, f.Props.NameRU, string(f.Geometry)); err != nil {
				return fmt.Errorf("upsert district kato %s: %w", kato, err)
			}
			imported++
		}

		fmt.Printf("imported %d districts from %s\n", imported, args[0])
		return nil
	},
}
