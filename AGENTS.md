# AGENTS instructions

- If you're not entirely sure how to use a library or module, look it up. Find the source code,
  docs, whatever. It's ok to ask the user for a link to the documentation or code if needed.
  - Noggin source code: githubRepo `fedora-infra/noggin`
  - FreeIPA FAS extensions (user/group attributes): githubRepo `fedora-infra/freeipa-fas`
  - python-freeipa: githubRepo `waldur/python-freeipa`
  - django-post-office: githubRepo `ui/django-post_office`. Use this for all email needs (sending emails, email templates, etc.)
  - django-ses: githubRepo `django-ses/django-ses`
- Add clean code with sensible comments. Consider what you're implementing and the context, as well
  as how you can generalize functions to reduce code duplication and sources of bugs.
- If you can reuse something already implemented elsewhere, do it. Add the least amount of code possible (but make sure all error conditions are covered!)

## Python Coding Guidelines

- Write for Python 3.14. Do NOT write code to support earlier versions of Python. Always use modern Python practices appropriate for Python 3.14.
- Always use full type annotations, generics, and other modern practices.
- Always use full, absolute imports for paths.
- ALWAYS use `@override` decorators to override methods from base classes.
  This is a modern Python practice and helps avoid bugs.
- Avoid writing trivial wrapper functions.
- Prefer f-strings over %-formatting.

### Types and Type Annotations

- Always use full type annotations, generics, and other modern practices.
- Use modern union syntax: `str | None` instead of `Optional[str]`, `dict[str]` instead
  of `Dict[str]`, `list[str]` instead of `List[str]`, etc.
- Never use/import `Optional` for new code.
- Use modern enums like `StrEnum` if appropriate.
- One exception to common practice on enums: If an enum has many values that are
  strings, and they have a literal value as a string (like in a JSON protocol), it’s
  fine to use lower_snake_case for enum values to match the actual value.
  This is more readable than LONG_ALL_CAPS_VALUES, and you can simply set the value to
  be the same as the name for each.
  For example:
  ```python
  class MediaType(Enum):
    """
    Media types. For broad categories only, to determine what processing
    is possible.
    """

    text = "text"
    image = "image"
    audio = "audio"
    video = "video"
    webpage = "webpage"
    binary = "binary"
  ```

### Guidelines for Comments

- Comments should be EXPLANATORY: Explain *WHY* something is done a certain way and not
  just *what* is done.

- Comments should be CONCISE: Remove all extraneous words.

- DO NOT use comments to state obvious things or repeat what is evident from the code.
  Here is an example of a comment that SHOULD BE REMOVED because it simply repeats the
  code, which is distracting and adds no value:
  ```python
  if self.failed == 0:
      # All successful
      return "All tasks finished successfully"
  ```

### Guidelines for Backward Compatibility

- When changing code in a library or general function, if a change to an API or library
  will break backward compatibility, MENTION THIS to the user.

- DO NOT implement additional code for backward compatiblity (such as extra methods or
  variable aliases or comments about backward compatibility) UNLESS the user has
  confirmed that it is necessary.

## Template Coding Guidelines

- Use `{% empty %}` in templates, instead of `{% if %}{% for _ in _ %}{% endfor %}{% else %}`
- Don't use javascript confirmations, there are modals defined in `_modal_*.html`
- If you add new email templates listed in `settings.py`, make sure they're listed in `configured_email_template_names`
- never use the character `’` (U+2019), use `'`.

## DRY + Single Source of Truth (required)

Before introducing new helpers/constants:
- Search the repo for existing equivalents (setting names, helper functions, payload shapes) and reuse them.
- Do not add “wrapper” functions that merely forward arguments or return `settings.*` unless they add real semantics and are used in 2+ places.
- Avoid convoluted constructions like `signed = username in { str(u).strip() for u in (getattr(agreement, "users", []) or []) if str(u).strip() }` when simply `signed = username in agreement.users` will do. You need to have a very valid reason for writing convoluted code.
- Avoid getattr (required)
  - Do not use `getattr()` for normal application code.
  - Prefer direct access (obj.attr, settings.X, module.NAME) and let errors surface during tests.
  - Only use `getattr()` when one of these is true:
    - You’re dealing with duck-typed / optional interfaces (e.g., template tags handling User | AnonymousUser | SimpleNamespace).
    - You’re interacting with threadlocals / request objects where the attribute may or may not exist; prefer `hasattr()` + direct access, or try/except AttributeError.
    - You’re probing optional third-party APIs (feature detection), where the attribute genuinely may not exist.
  - If you use `getattr()`, you must:
    - Add a short comment explaining why direct access isn’t safe here.
    - Avoid “double defaults” (don’t mirror defaults already defined in settings or upstream data prep).
- Treat any new `getattr()` in core app code as a regression unless justified by one of the allowed cases above.

When you notice duplicated logic across files:
- Refactor only if it reduces the number of implementations/branches. Moving code into a new module is not enough if the same logic still exists in multiple wrappers.
- Prefer a small shared primitive API (e.g. `make_signed_token(payload)` / `read_signed_token(token)`) over per-feature wrappers.
- Prefer removing indirection over adding it (don’t introduce `_ttl()` / `_salt()` helpers that just return settings).

Guardrails:
- Avoid “double defaults” (a default in settings + another default in code) because it silently diverges.
- Prefer deleting code over adding code during refactors.
- If a fix starts ballooning into lots of new production code, pause and reassess. Prefer the smallest change that satisfies the requirement, and avoid adding new layers/abstractions unless the product behavior truly needs it.

When tests/mocks constrain refactors:
- Prefer the smallest change that satisfies the failing test (or new requirement). Why: tests encode current behavior and constraints; working with them reduces risk and review overhead.
- Do not introduce new abstractions, layers, or reshaped APIs just to make a refactor feel “cleaner”. Why: this is a common source of scope creep and makes future changes harder to reason about.
- If a larger redesign is genuinely needed, stop and surface it explicitly (what breaks, what needs to change, what the migration plan is). Why: it should be a deliberate decision, not an incidental byproduct of a bugfix.
- Do not expand mocks/stubs to accommodate a new architecture unless the product behavior changed. Why: tests should validate behavior, not be rewritten to follow an unrequested design.

Pre-change checklist (must answer mentally before finishing):
- Did I add a fallback that’s already configured elsewhere?
- Did I reduce the number of implementations, or just relocate code?
- Can any new wrappers be deleted without changing call sites? If yes, delete them.

- Do Test-Driven development: Add tests before you implement a new feature, make sure they cover all the
  failure scenarios and that they really *do* fail. Then implement the new feature and make sure your tests pass.
  - Whenever I report a problem or request a new feature, I WANT YOU TO CREATE A TEST CASE FIRST, RUN THE TESTS TO SHOW THE FAILURE, AND ONLY THEN DO YOU FIX IT (and then run the tests again)
- DO NOT write trivial or obvious tests that are evident directly from code, such as
  assertions that confirm the value of a constant setting.
- DO NOT write trivial tests that test something we know already works, like
  instantiating a Pydantic object.

## Test tips
- You don't need to restart the web container after code changes; it refreshes automatically.
- This is python, you don't need to compile the code.
- You can smoke-test Django with: `podman-compose exec -T web python manage.py check`
- You can run more tests with: `podman-compose exec -T web python manage.py test`
- Add ruff after you're done making changes: `podman-compose exec -T web ruff check --fix /app/astra_app`
- Stop and restart everything: `podman-compose down && podman-compose up -d --build`. NEVER RUN `podman-compose down -v` as that will delete your database!
