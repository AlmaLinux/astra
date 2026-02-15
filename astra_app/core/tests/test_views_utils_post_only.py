from django.http import Http404, HttpResponse
from django.test import RequestFactory, SimpleTestCase

from core.views_utils import post_only_404, require_post_or_404


class PostOnly404GuardTests(SimpleTestCase):
    def test_require_post_or_404_raises_http404_for_non_post(self) -> None:
        request = RequestFactory().get("/")

        with self.assertRaisesMessage(Http404, "Not found"):
            require_post_or_404(request)

    def test_require_post_or_404_allows_post(self) -> None:
        request = RequestFactory().post("/")

        require_post_or_404(request)

    def test_post_only_404_decorator_blocks_non_post(self) -> None:
        @post_only_404
        def _view(_request):
            return HttpResponse("ok")

        request = RequestFactory().get("/")

        with self.assertRaisesMessage(Http404, "Not found"):
            _view(request)

    def test_post_only_404_decorator_allows_post(self) -> None:
        @post_only_404
        def _view(_request):
            return HttpResponse("ok")

        request = RequestFactory().post("/")

        response = _view(request)

        self.assertEqual(response.status_code, 200)
