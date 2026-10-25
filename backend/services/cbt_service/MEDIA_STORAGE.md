# Uploaded media (question & option images)

The rich-text editor uploads images to `POST /api/v1/uploads/images`
(teacher, admin, super admin). The service checks the file's bytes
(PNG, JPEG, GIF or WebP only; SVG is refused because it can carry scripts),
stores it under `orgs/{org_id}/images/{yyyy}/{mm}/{random}.{ext}`, and returns a
public URL that the editor embeds in the question HTML.

## Layout: one bucket, one folder per organisation

```
orgs/
  {org_id}/            <- everything one school stores
    images/2026/10/3f2a...png
```

All of a school's media sits under `orgs/{org_id}/`, so these are single
operations on that prefix: delete a school's files, export or hand them over,
measure its storage use, or move it to another bucket or server. The org ID is
used rather than the school's name because names change and IDs don't.
New kinds of files (logos, exports) should go under the same prefix, built with
`org_media_prefix()` in `services/media/image_service.py`.

One shared bucket is deliberate. Separate buckets per school add setup, CDN and
key management for each one, and Spaces limits buckets per account. Use one only
if a school must have its own storage account or contractual data separation.

Pick where files go with `STORAGE_BACKEND` in `.env`.

## The one decision that matters: `MEDIA_PUBLIC_BASE_URL`

The URL is saved **inside the question HTML**, so whatever prefix you use
becomes permanent. Set it to a domain you control, for example
`https://media.juwazi.com`, and point that domain at wherever the files live
(Spaces CDN today, an nginx folder tomorrow). Moving storage is then a DNS
change plus copying files, with no database rewrite. If you leave it empty,
you get the provider's own URL, which ties existing content to that provider.

## Option A: DigitalOcean Spaces (cloud)

```env
STORAGE_BACKEND=spaces
SPACES_KEY=...            # Spaces access key (API > Spaces Keys)
SPACES_SECRET=...
SPACES_BUCKET=juwazi-media
SPACES_REGION=fra1        # region the Space was created in (fra1, lon1, ams3, nyc3, sgp1, ...)
# Optional:
MEDIA_PUBLIC_BASE_URL=https://media.juwazi.com   # custom CDN domain on the Space
# default: https://{bucket}.{region}.cdn.digitaloceanspaces.com
```

In the DigitalOcean panel:
1. Create the Space and **enable the CDN** on it (the default URL above uses it).
2. Optionally add a custom subdomain to the CDN and use it as `MEDIA_PUBLIC_BASE_URL`.
3. Create a Spaces access key **limited to this bucket** if your account allows it.

Objects are uploaded `public-read` with a one-year immutable cache header.
Keys are random, so URLs can't be guessed, but anyone who has a URL can view the image.

## Option B: a folder on your own server

```env
STORAGE_BACKEND=local
MEDIA_ROOT=/var/lib/juwazi/media              # any folder the app can write to
MEDIA_PUBLIC_BASE_URL=https://cbt.yourschool.local/media
MEDIA_SERVE_LOCAL=false                       # nginx serves the folder (recommended)
```

**Recommended: let nginx serve the folder.** It's faster than Python, handles
caching, and keeps image traffic off the API workers:

```nginx
location /media/ {
    alias /var/lib/juwazi/media/;
    expires 1y;
    add_header Cache-Control "public, immutable";
    add_header X-Content-Type-Options nosniff;
    autoindex off;
}
```

**Simplest (development, small installs):** leave `MEDIA_SERVE_LOCAL=true` and
the API serves the folder itself at `MEDIA_URL_PATH` (default `/media`), e.g.
`http://localhost:8000/media/...`. This is the default, so local development
needs no configuration.

Back up `MEDIA_ROOT` together with the database: the HTML references these files.

## Not handled yet
- Deleting images: removing an image from a question leaves the file in storage.
  A periodic clean-up job can delete keys not referenced by any item.
