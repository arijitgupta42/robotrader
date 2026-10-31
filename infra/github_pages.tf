# The project website is built by a GitHub Actions workflow (.github/workflows/pages.yml) and served by GitHub
# Pages.  The workflow downloads the website's data (site/ in the data bucket, written by the stock-selection
# Lambda) with a short-lived role it assumes through GitHub's OIDC provider, so no AWS keys are stored in GitHub.
#
# The role trusts only workflow jobs on this repository's main branch, and only reads site/.  The workflow's
# build job must NOT use a GitHub environment: a job with `environment:` gets the subject
# "repo:<owner>/<repo>:environment:<name>" instead of the branch ref, and this trust policy would refuse it.
# Keep the AWS step in the build job; only the deploy job uses the github-pages environment.
#
# Cost: none (OIDC providers and IAM roles are free; the workflow's few S3 GETs a week are inside the free tier).

resource "aws_iam_openid_connect_provider" "github" {
  url            = "https://token.actions.githubusercontent.com"
  client_id_list = ["sts.amazonaws.com"]
}

data "aws_iam_policy_document" "github_pages_assume" {
  statement {
    actions = ["sts:AssumeRoleWithWebIdentity"]
    principals {
      type        = "Federated"
      identifiers = [aws_iam_openid_connect_provider.github.arn]
    }
    condition {
      test     = "StringEquals"
      variable = "token.actions.githubusercontent.com:aud"
      values   = ["sts.amazonaws.com"]
    }
    condition {
      test     = "StringEquals"
      variable = "token.actions.githubusercontent.com:sub"
      values   = ["repo:${var.github_repo}:ref:refs/heads/main"]
    }
  }
}

resource "aws_iam_role" "github_pages" {
  name                 = "robotrader-github-pages-data"
  description          = "Assumed by the GitHub Pages workflow on ${var.github_repo} main to read the website's data (site/)."
  assume_role_policy   = data.aws_iam_policy_document.github_pages_assume.json
  max_session_duration = 3600
}

data "aws_iam_policy_document" "github_pages_permissions" {
  statement {
    sid       = "ListSiteData"
    actions   = ["s3:ListBucket"]
    resources = [aws_s3_bucket.data.arn]
    condition {
      test     = "StringLike"
      variable = "s3:prefix"
      values   = ["site/", "site/*"]
    }
  }

  statement {
    sid       = "ReadSiteData"
    actions   = ["s3:GetObject"]
    resources = ["${aws_s3_bucket.data.arn}/site/*"]
  }
}

resource "aws_iam_role_policy" "github_pages" {
  name   = "robotrader-github-pages-read-site-data"
  role   = aws_iam_role.github_pages.id
  policy = data.aws_iam_policy_document.github_pages_permissions.json
}
