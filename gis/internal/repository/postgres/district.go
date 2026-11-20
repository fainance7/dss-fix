package postgres

import (
	"context"

	"gis/internal/domain"

	"github.com/google/uuid"
)

// ListAllObservations returns every observation for a dataset ordered by
// (kato_code, start_date). Unlike ListObservations it is unpaginated: it backs GeoJSON
// assembly, where all of a dataset's observations are needed at once.
func (r *DatasetRepository) ListAllObservations(ctx context.Context, datasetID uuid.UUID) ([]domain.Observation, error) {
	rows, err := r.pool.Query(ctx,
		`SELECT `+observationColumns+`
		 FROM dataset_observations
		 WHERE dataset_id = $1
		 ORDER BY kato_code, start_date`,
		datasetID)
	if err != nil {
		return nil, mapError(err)
	}
	defer rows.Close()

	out := make([]domain.Observation, 0)
	for rows.Next() {
		o, err := scanObservation(rows)
		if err != nil {
			return nil, mapError(err)
		}
		out = append(out, o)
	}
	return out, mapError(rows.Err())
}

// DistrictGeometriesByKato returns the districts whose KATO codes are in katos,
// keyed by KATO, with each boundary serialized as a GeoJSON geometry object in
// EPSG:4326. KATO codes with no matching district are simply absent from the
// result map.
func (r *DatasetRepository) DistrictGeometriesByKato(ctx context.Context, katos []string) (map[string]domain.District, error) {
	out := make(map[string]domain.District)
	if len(katos) == 0 {
		return out, nil
	}

	rows, err := r.pool.Query(ctx,
		`SELECT kato, name, ST_AsGeoJSON(coordinates)::jsonb
		 FROM districts
		 WHERE kato = ANY($1)`,
		katos)
	if err != nil {
		return nil, mapError(err)
	}
	defer rows.Close()

	for rows.Next() {
		var d domain.District
		if err := rows.Scan(&d.Kato, &d.Name, &d.Geometry); err != nil {
			return nil, mapError(err)
		}
		out[d.Kato] = d
	}
	return out, mapError(rows.Err())
}

// DistrictExtent returns the extent (EPSG:4326) of all imported district
// boundaries — the area of interest for import validation (Этап 7, ТЗ п. 4.4).
func (r *DatasetRepository) DistrictExtent(ctx context.Context) ([4]float64, bool, error) {
	var b [4]float64
	var minx, miny, maxx, maxy *float64
	err := r.pool.QueryRow(ctx,
		`SELECT ST_XMin(e), ST_YMin(e), ST_XMax(e), ST_YMax(e)
		   FROM (SELECT ST_Extent(coordinates)::box2d AS e FROM districts) t`,
	).Scan(&minx, &miny, &maxx, &maxy)
	if err != nil {
		return b, false, err
	}
	if minx == nil {
		return b, false, nil
	}
	return [4]float64{*minx, *miny, *maxx, *maxy}, true, nil
}
