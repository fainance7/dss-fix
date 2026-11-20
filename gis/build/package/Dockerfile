# Build context is the repo root: docker build -f build/package/Dockerfile .
FROM golang:1.26-alpine3.24 AS builder

# godal (GDAL bindings) needs cgo, a C/C++ toolchain and the GDAL headers.
# g++ is required: parts of the binding compile as C++ against GDAL's headers.
RUN apk add --no-cache gdal-dev gcc g++ musl-dev pkgconfig

WORKDIR /src

COPY go.mod go.sum ./
RUN go mod download

COPY . .
RUN CGO_ENABLED=1 GOOS=linux go build -trimpath -o /out/gis ./cmd/gis

# Must stay on the same Alpine release as the builder — hence the pinned
# `golang:1.26-alpine3.24` above rather than the floating `-alpine` tag. A
# mismatch ships a different libgdal soname, and the image still builds green
# while the binary dies at startup with "libgdal.so.NN: No such file".
# Bump both lines together.
FROM alpine:3.24

# gdal: shared library for godal (raster math in the worker).
# gdal-tools: gdal_translate / gdalinfo (COG conversion) and ogr2ogr.
RUN apk add --no-cache ca-certificates tzdata gdal gdal-tools \
    && adduser -D -u 10001 app

WORKDIR /app
COPY --from=builder /out/gis /usr/local/bin/gis

USER app

EXPOSE 8080

ENTRYPOINT ["gis"]
CMD ["serve"]
