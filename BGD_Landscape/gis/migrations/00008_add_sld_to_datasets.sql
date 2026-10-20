-- +goose Up
-- Optional SLD (Styled Layer Descriptor) style file for a dataset. Applies to
-- every file_type; a dataset may have at most one (uploading again replaces
-- it).
ALTER TABLE datasets ADD COLUMN sld_storage_key TEXT;
ALTER TABLE datasets ADD COLUMN sld_filename VARCHAR(255);
ALTER TABLE datasets ADD COLUMN sld_content_type VARCHAR(255);

-- +goose Down
ALTER TABLE datasets DROP COLUMN sld_storage_key;
ALTER TABLE datasets DROP COLUMN sld_filename;
ALTER TABLE datasets DROP COLUMN sld_content_type;
