package http

// Paginated is the generic list response envelope.
type Paginated[T any] struct {
	Data       []T `json:"data"`
	Page       int `json:"page"`
	PageSize   int `json:"page_size"`
	Total      int `json:"total"`
	TotalPages int `json:"total_pages"`
}

// newPaginated builds a Paginated envelope, computing total pages and
// normalizing a nil slice to an empty array.
func newPaginated[T any](items []T, page, pageSize, total int) Paginated[T] {
	totalPages := 0
	if pageSize > 0 {
		totalPages = (total + pageSize - 1) / pageSize
	}
	if items == nil {
		items = []T{}
	}
	return Paginated[T]{
		Data:       items,
		Page:       page,
		PageSize:   pageSize,
		Total:      total,
		TotalPages: totalPages,
	}
}
