# Mosaic

A simple blog system in the spirit of the <a href="https://indieweb.org">IndieWeb</a>. It's aimed to get up and running as quickly as possible with your own, easily customizable CMS

## Installation

First, install the package using your favorite python package manager

`uv add django-mosaic`

or

`pip install django-mosaic`

Second, you need to enable the app in your Django project.

```python
# settings.py
INSTALLED_APPS = [
  ...
  "django_mosaic"
  ...
]
```

Third, run the migrations to add the relevant schemas to your database

```
uv run python manage.py migrate
```

## Quickstart

Start the development server.

```
uv run python manage.py runserver
```

Mosaic exposes all its features through the admin. First, create a user for the admin.

```
uv run python manage.py createsuperuser
```

Go to <a href="http://localhost:8000/admin">http://localhost:8000/admin"</a>.

![[docs/img/01-admin.png]]

You can write a post right within the admin, in [markdown](https://daringfireball.net/projects/markdown/).

![[docs/img/02-create-post.png]]

By default, there are two `namespace`s, `public` and `private`. Everything you post in `public` will be visible to, well, everyone. Posts in `private` will be visible only to those with a secret `AccessToken`, which you can also generate in the admin.

Only `Post`s with the `is_draft` flag set to `False` will be shown on your website.

Hit the save button and go to [https://localhost:8000] 

![[docs/img/03-home.png]]

Have fun!
