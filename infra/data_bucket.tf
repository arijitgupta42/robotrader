# The scout's results bucket: successful/ (scout results), failed/, snapshots/ (Universe Snapshots) and reports/
# (quarterly backtest reports).  It was created by hand and is adopted here with import blocks (imports.tf).

resource "aws_s3_bucket" "data" {
  bucket = var.data_bucket

  lifecycle {
    prevent_destroy = true # every week's results live here
  }
}

resource "aws_s3_bucket_versioning" "data" {
  bucket = aws_s3_bucket.data.id
  versioning_configuration {
    status = "Enabled"
  }
}

resource "aws_s3_bucket_server_side_encryption_configuration" "data" {
  bucket = aws_s3_bucket.data.id
  rule {
    bucket_key_enabled = true
    apply_server_side_encryption_by_default {
      sse_algorithm = "AES256"
    }
  }
}

resource "aws_s3_bucket_public_access_block" "data" {
  bucket                  = aws_s3_bucket.data.id
  block_public_acls       = true
  ignore_public_acls      = true
  block_public_policy     = true
  restrict_public_buckets = true
}

resource "aws_s3_bucket_ownership_controls" "data" {
  bucket = aws_s3_bucket.data.id
  rule {
    object_ownership = "BucketOwnerEnforced"
  }
}

# snapshots/ and reports/ are kept for ever (they are the backtest's data).  Old versions of overwritten objects
# (a re-backfill replaces every snapshot) are not needed, so they expire, which keeps storage at a few MB.
resource "aws_s3_bucket_lifecycle_configuration" "data" {
  bucket = aws_s3_bucket.data.id

  rule {
    id     = "expire-successful"
    status = "Enabled"
    filter {
      prefix = "successful/"
    }
    expiration {
      days = 365
    }
  }

  rule {
    id     = "expire-failed"
    status = "Enabled"
    filter {
      prefix = "failed/"
    }
    expiration {
      days = 30
    }
  }

  rule {
    id     = "expire-old-versions"
    status = "Enabled"
    filter {}
    noncurrent_version_expiration {
      noncurrent_days = 30
    }
    abort_incomplete_multipart_upload {
      days_after_initiation = 7
    }
  }

  depends_on = [aws_s3_bucket_versioning.data]
}
