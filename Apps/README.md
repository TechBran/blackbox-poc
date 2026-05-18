# Apps/

Customer-created sub-apps live here at runtime. The BlackBox Portal's
`/agent/apps/register` endpoint adds each app's metadata to
`Manifest/apps.json`, and the reverse-proxy at `/app-proxy/<port>/`
serves them.

## What's tracked here

Almost nothing. `.gitignore` excludes everything under `Apps/*` except:

- This `README.md`
- `.gitkeep` (keeps the directory in git so fresh clones get an `Apps/` to write into)
- `PelvicVibeAndroid/` (a grandfathered Android example — predates the
  ignore rule)

## Why everything else is ignored

The update pipeline does `git reset --hard origin/main` to pull new code.
If a future repo commit added `Apps/foo/` and a customer had already
created `Apps/foo/`, reset would silently destroy the customer's app.

Ignoring `Apps/*` makes the collision impossible — customer apps are
invisible to git, so reset has nothing to overwrite.

## Where new example apps should go

`docs/examples/` if they're documentation-grade. `Portal/uploads/apps/`
if they're runtime-shipped. Neither path collides with customer-created
content.
