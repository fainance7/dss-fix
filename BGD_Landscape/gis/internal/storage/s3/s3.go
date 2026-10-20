// Package s3 wraps the MinIO client to provide object storage for datasets.
package s3

import (
	"context"
	"fmt"
	"io"

	"gis/internal/config"

	"github.com/minio/minio-go/v7"
	"github.com/minio/minio-go/v7/pkg/credentials"
)

// Client stores and retrieves dataset objects in an S3-compatible bucket.
type Client struct {
	mc     *minio.Client
	bucket string
}

// New constructs a Client and ensures the configured bucket exists.
func New(ctx context.Context, cfg config.S3Config) (*Client, error) {
	mc, err := minio.New(cfg.Endpoint, &minio.Options{
		Creds:  credentials.NewStaticV4(cfg.AccessKey, cfg.SecretKey, ""),
		Secure: cfg.UseSSL,
	})
	if err != nil {
		return nil, fmt.Errorf("create s3 client: %w", err)
	}

	exists, err := mc.BucketExists(ctx, cfg.Bucket)
	if err != nil {
		return nil, fmt.Errorf("check bucket: %w", err)
	}
	if !exists {
		if err := mc.MakeBucket(ctx, cfg.Bucket, minio.MakeBucketOptions{}); err != nil {
			return nil, fmt.Errorf("make bucket: %w", err)
		}
	}

	return &Client{mc: mc, bucket: cfg.Bucket}, nil
}

// Put streams an object of the given size to the bucket under key.
func (c *Client) Put(ctx context.Context, key string, r io.Reader, size int64, contentType string) error {
	_, err := c.mc.PutObject(ctx, c.bucket, key, r, size, minio.PutObjectOptions{
		ContentType: contentType,
	})
	if err != nil {
		return fmt.Errorf("put object %q: %w", key, err)
	}
	return nil
}

// Get returns a reader for the object stored under key. The caller must close it.
func (c *Client) Get(ctx context.Context, key string) (io.ReadCloser, error) {
	obj, err := c.mc.GetObject(ctx, c.bucket, key, minio.GetObjectOptions{})
	if err != nil {
		return nil, fmt.Errorf("get object %q: %w", key, err)
	}
	return obj, nil
}

// Remove deletes the object stored under key.
func (c *Client) Remove(ctx context.Context, key string) error {
	if err := c.mc.RemoveObject(ctx, c.bucket, key, minio.RemoveObjectOptions{}); err != nil {
		return fmt.Errorf("remove object %q: %w", key, err)
	}
	return nil
}

// Ping verifies connectivity to the object store (used by readiness checks).
func (c *Client) Ping(ctx context.Context) error {
	_, err := c.mc.BucketExists(ctx, c.bucket)
	return err
}
