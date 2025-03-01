# Copyright © Michal Čihař <michal@weblate.org>
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""Test for search views."""

import re

from django.http import QueryDict
from django.test.utils import override_settings
from django.urls import reverse

from weblate.trans.models import Component
from weblate.trans.tests.test_views import ViewTestCase
from weblate.utils.db import using_postgresql
from weblate.utils.ratelimit import reset_rate_limit
from weblate.utils.state import STATE_FUZZY, STATE_READONLY, STATE_TRANSLATED


class SearchViewTest(ViewTestCase):
    @classmethod
    def _databases_support_transactions(cls):
        # This is workaround for MySQL as FULL TEXT index does not work
        # well inside a transaction, so we avoid using transactions for
        # tests. Otherwise we end up with no matches for the query.
        # See https://dev.mysql.com/doc/refman/5.6/en/innodb-fulltext-index.html
        if not using_postgresql():
            return False
        return super()._databases_support_transactions()

    def setUp(self):
        super().setUp()
        self.translation = self.component.translation_set.get(language_code="cs")
        self.translate_url = self.translation.get_translate_url()
        self.update_fulltext_index()
        reset_rate_limit("search", address="127.0.0.1")

    def do_search(self, params, expected, url=None):
        """Helper method for performing search test."""
        if url is None:
            url = self.translate_url
        response = self.client.get(url, params)
        if expected is None:
            self.assertRedirects(response, self.translation.get_absolute_url())
        else:
            self.assertContains(response, expected)
        return response

    def do_search_url(self, url):
        """Test search on given URL."""
        response = self.client.get(url, {"q": "hello"})
        self.assertContains(response, '<span class="hlmatch">Hello</span>, world')
        response = self.client.get(url, {"q": "changed:>=2010-01-10"})
        self.assertContains(response, "2010-01-10")

    @override_settings(RATELIMIT_SEARCH_ATTEMPTS=20000)
    def test_all_search(self):
        """Searching in all projects."""
        response = self.client.get(reverse("search"), {"q": "hello"})
        self.assertContains(response, '<span class="hlmatch">Hello</span>, world')
        response = self.client.get(reverse("search"), {"q": 'source:r"^Hello"'})
        self.assertContains(response, "Hello, world")
        response = self.client.get(reverse("search"), {"q": 'source:r"^(Hello"'})
        self.assertContains(response, "Invalid regular expression")
        response = self.client.get(
            reverse("search"), {"q": "hello AND state:<translated"}
        )
        self.assertContains(response, "Hello, world")
        response = self.client.get(reverse("search"), {"q": "hello AND state:empty"})
        self.assertContains(response, "Hello, world")
        response = self.client.get(reverse("search"), {"q": "check:php_format"})
        self.assertContains(response, "No matching strings found.")
        response = self.client.get(
            reverse("search"), {"q": "check:php_format", "ignored": "1"}
        )
        self.assertContains(response, "No matching strings found.")
        self.do_search_url(reverse("search"))

    def test_pagination(self):
        response = self.client.get(reverse("search"), {"q": "hello", "page": 1})
        self.assertContains(response, '<span class="hlmatch">Hello</span>, world')
        response = self.client.get(reverse("search"), {"q": "hello", "page": 10})
        self.assertContains(response, '<span class="hlmatch">Hello</span>, world')
        response = self.client.get(reverse("search"), {"q": "hello", "page": "x"})
        self.assertContains(response, '<span class="hlmatch">Hello</span>, world')

    def test_language_search(self):
        """Searching in all projects."""
        response = self.client.get(reverse("search"), {"q": "hello", "lang": "cs"})
        self.assertContains(response, '<span class="hlmatch">Hello</span>, world')

    def test_project_search(self):
        """Searching within project."""
        self.do_search_url(
            reverse("search", kwargs={"path": self.project.get_url_path()})
        )

    def test_component_search(self):
        """Searching within component."""
        self.do_search_url(reverse("search", kwargs=self.kw_component))

    def test_project_language_search(self):
        """Searching within project."""
        self.do_search_url(
            reverse("search", kwargs={"path": [self.project.slug, "-", "cs"]})
        )

    def test_translation_search(self):
        """Searching within translation."""
        # Default
        self.do_search({"q": "source:hello"}, "source:hello")
        # Short exact
        self.do_search({"q": "x", "search": "exact"}, None)

    def test_review(self):
        # Review
        self.do_search({"q": "changed:>=2010-01-10"}, None)
        self.do_search({"q": "changed:>=2010-01-10 AND NOT changed_by:testuser"}, None)
        self.do_search({"q": "changed:>2010-01-10 AND changed_by:testuser"}, None)
        self.do_search({"q": "changed_by:testuser"}, None)
        # Review, partial date
        self.do_search({"q": "changed:>=2010-01-"}, "Unknown string format: 2010-01-")

    def extract_params(self, response):
        search_url = re.findall(r'data-params="([^"]*)"', response.content.decode())[0]
        return QueryDict(search_url, mutable=True)

    def test_search_links(self):
        response = self.do_search({"q": "source:Weblate"}, "source:Weblate")
        # Extract search URL
        params = self.extract_params(response)
        # Try access to pages
        params["offset"] = 1
        response = self.client.get(self.translate_url, params)
        self.assertContains(response, "https://demo.weblate.org/")
        params["offset"] = 2
        response = self.client.get(self.translate_url, params)
        self.assertContains(response, "Thank you for using Weblate.")
        # Invalid offset
        params["offset"] = "bug"
        response = self.client.get(self.translate_url, params)
        self.assertContains(response, "https://demo.weblate.org/")
        # Go to end
        params["offset"] = 3
        response = self.client.get(self.translate_url, params)
        self.assertRedirects(response, self.translation.get_absolute_url())
        # Try no longer cached query (should be deleted above)
        params["offset"] = 2
        response = self.client.get(self.translate_url, params)
        self.assertContains(response, "Thank you for using Weblate.")

    def test_search_checksum(self):
        unit = self.translation.unit_set.get(
            source="Try Weblate at <https://demo.weblate.org/>!\n"
        )
        self.do_search({"checksum": unit.checksum}, "3 / 4")

    def test_search_offset(self):
        """Test offset navigation."""
        self.do_search({"offset": 1}, "1 / 4")
        self.do_search({"offset": 4}, "4 / 4")
        self.do_search({"offset": 5}, None)

    def test_search_type(self):
        self.do_search({"q": "state:<translated"}, "Unfinished strings")
        self.do_search({"q": "state:needs-editing"}, None)
        self.do_search({"q": "has:suggestion"}, None)
        self.do_search({"q": "has:check"}, None)
        self.do_search({"q": "check:plurals"}, None)
        self.do_search({"q": ""}, "1 / 4")

    def test_search_plural(self):
        response = self.do_search({"q": "banana"}, "banana")
        self.assertContains(response, "One")
        self.assertContains(response, "Few")
        self.assertContains(response, "Other")
        self.assertNotContains(response, "Plural form ")

    def test_checksum(self):
        self.do_search({"checksum": "invalid"}, None)


class ReplaceTest(ViewTestCase):
    """Test for search and replace functionality."""

    def setUp(self):
        super().setUp()
        self.edit_unit("Hello, world!\n", "Nazdar svete!\n")
        self.unit = self.get_unit()

    def do_replace_test(self, url, confirm=True, query=None):
        query = query or ""
        response = self.client.post(
            url, {"q": query, "search": "Nazdar", "replacement": "Ahoj"}, follow=True
        )
        self.assertContains(
            response, "Please review and confirm the search and replace results."
        )
        payload = {
            "q": query,
            "search": "Nazdar",
            "replacement": "Ahoj",
            "confirm": "1",
        }
        if confirm:
            payload["units"] = self.unit.pk
        response = self.client.post(url, payload, follow=True)
        unit = self.get_unit()
        if confirm:
            self.assertContains(
                response, "Search and replace completed, 1 string was updated."
            )
            self.assertEqual(unit.target, "Ahoj svete!\n")
        else:
            self.assertContains(
                response, "Search and replace completed, no strings were updated."
            )
            self.assertEqual(unit.target, "Nazdar svete!\n")

    def test_no_match(self):
        response = self.client.post(
            reverse("replace", kwargs=self.kw_translation),
            {"search": "Ahoj", "replacement": "Cau"},
            follow=True,
        )
        self.assertContains(
            response, "Search and replace completed, no strings were updated."
        )
        unit = self.get_unit()
        self.assertEqual(unit.target, "Nazdar svete!\n")

    def test_replace_translated(self):
        self.do_replace_test(
            reverse("replace", kwargs=self.kw_translation),
            "is:translated",
        )

    def test_replace_nontranslated(self):
        response = self.client.post(
            reverse("replace", kwargs=self.kw_translation),
            {"q": "NOT is:translated", "search": "Nazdar", "replacement": "Ahoj"},
            follow=True,
        )
        self.assertNotContains(
            response, "Please review and confirm the search and replace results."
        )

    def test_replace(self):
        self.do_replace_test(reverse("replace", kwargs=self.kw_translation))

    def test_replace_project(self):
        self.do_replace_test(
            reverse("replace", kwargs={"path": self.project.get_url_path()})
        )

    def test_replace_component(self):
        self.do_replace_test(reverse("replace", kwargs=self.kw_component))

    def test_replace_project_language(self):
        self.do_replace_test(
            reverse(
                "replace",
                kwargs={
                    "path": (self.project.slug, "-", self.translation.language.code)
                },
            )
        )


class BulkEditTest(ViewTestCase):
    """Test for build edit functionality."""

    def setUp(self):
        super().setUp()
        self.edit_unit("Hello, world!\n", "Nazdar svete!\n", fuzzy=True)
        self.unit = self.get_unit()
        self.make_manager()

    def do_bulk_edit_test(self, url):
        response = self.client.post(
            url, {"q": "state:needs-editing", "state": STATE_TRANSLATED}, follow=True
        )
        self.assertContains(response, "Bulk edit completed, 1 string was updated.")
        self.assertEqual(self.get_unit().state, STATE_TRANSLATED)

    def test_no_match(self):
        response = self.client.post(
            reverse("bulk-edit", kwargs={"path": self.project.get_url_path()}),
            {"q": "state:approved", "state": STATE_FUZZY},
            follow=True,
        )
        self.assertContains(response, "Bulk edit completed, no strings were updated.")
        unit = self.get_unit()
        self.assertEqual(unit.state, STATE_FUZZY)

    def test_bulk_edit(self):
        self.do_bulk_edit_test(reverse("bulk-edit", kwargs=self.kw_translation))

    def test_bulk_edit_project(self):
        self.do_bulk_edit_test(
            reverse("bulk-edit", kwargs={"path": self.project.get_url_path()})
        )

    def test_bulk_edit_component(self):
        self.do_bulk_edit_test(reverse("bulk-edit", kwargs=self.kw_component))

    def test_bulk_edit_project_language(self):
        self.do_bulk_edit_test(
            reverse(
                "bulk-edit",
                kwargs={
                    "path": (self.project.slug, "-", self.translation.language.code)
                },
            )
        )

    def test_bulk_flags(self):
        response = self.client.post(
            reverse("bulk-edit", kwargs={"path": self.project.get_url_path()}),
            {"q": "state:needs-editing", "state": -1, "add_flags": "python-format"},
            follow=True,
        )
        self.assertContains(response, "Bulk edit completed, 1 string was updated.")
        unit = self.get_unit()
        self.assertIn("python-format", unit.all_flags)
        response = self.client.post(
            reverse("bulk-edit", kwargs={"path": self.project.get_url_path()}),
            {"q": "state:needs-editing", "state": -1, "remove_flags": "python-format"},
            follow=True,
        )
        self.assertContains(response, "Bulk edit completed, 1 string was updated.")
        unit = self.get_unit()
        self.assertNotIn("python-format", unit.all_flags)

    def test_bulk_read_only(self):
        response = self.client.post(
            reverse("bulk-edit", kwargs={"path": self.project.get_url_path()}),
            {"q": "language:en", "state": -1, "add_flags": "read-only"},
            follow=True,
        )
        self.assertContains(response, "Bulk edit completed, 4 strings were updated.")
        unit = self.get_unit()
        self.assertIn("read-only", unit.all_flags)
        response = self.client.post(
            reverse("bulk-edit", kwargs={"path": self.project.get_url_path()}),
            {"q": "language:en", "state": -1, "remove_flags": "read-only"},
            follow=True,
        )
        self.assertContains(response, "Bulk edit completed, 4 strings were updated.")
        unit = self.get_unit()
        self.assertNotIn("read-only", unit.all_flags)

    def test_bulk_labels(self):
        label = self.project.label_set.create(name="Test label", color="black")
        response = self.client.post(
            reverse("bulk-edit", kwargs={"path": self.project.get_url_path()}),
            {"q": "state:needs-editing", "state": -1, "add_labels": label.pk},
            follow=True,
        )
        self.assertContains(response, "Bulk edit completed, 1 string was updated.")
        response = self.client.post(
            reverse("bulk-edit", kwargs={"path": self.project.get_url_path()}),
            {"q": "state:needs-editing", "state": -1, "add_labels": label.pk},
            follow=True,
        )
        self.assertContains(response, "Bulk edit completed, no strings were updated.")
        unit = self.get_unit()
        self.assertIn(label, unit.all_labels)
        self.assertEqual(getattr(unit.translation.stats, f"label:{label.name}"), 1)
        # Clear local outdated cache
        unit.source_unit.translation.stats.clear()
        self.assertEqual(
            getattr(unit.source_unit.translation.stats, f"label:{label.name}"),
            1,
        )
        response = self.client.post(
            reverse("bulk-edit", kwargs={"path": self.project.get_url_path()}),
            {"q": "state:needs-editing", "state": -1, "remove_labels": label.pk},
            follow=True,
        )
        self.assertContains(response, "Bulk edit completed, 1 string was updated.")
        response = self.client.post(
            reverse("bulk-edit", kwargs={"path": self.project.get_url_path()}),
            {"q": "state:needs-editing", "state": -1, "remove_labels": label.pk},
            follow=True,
        )
        self.assertContains(response, "Bulk edit completed, no strings were updated.")
        unit = self.get_unit()
        self.assertNotIn(label, unit.labels.all())
        self.assertEqual(getattr(unit.translation.stats, f"label:{label.name}"), 0)
        # Clear local outdated cache
        unit.source_unit.translation.stats.clear()
        self.assertEqual(
            getattr(unit.source_unit.translation.stats, f"label:{label.name}"),
            0,
        )

    def test_bulk_translation_label(self):
        label = self.project.label_set.create(
            name="Automatically translated", color="black"
        )
        unit = self.get_unit()
        unit.labels.add(label)
        # Clear local outdated cache
        unit.translation.stats.clear()
        self.assertEqual(
            getattr(unit.translation.stats, f"label:{label.name}"),
            1,
        )
        response = self.client.post(
            reverse("bulk-edit", kwargs={"path": self.project.get_url_path()}),
            {"q": "state:>=empty", "state": -1, "remove_labels": label.pk},
            follow=True,
        )
        self.assertContains(response, "Bulk edit completed, 1 string was updated.")
        unit = self.get_unit()
        self.assertNotIn(label, unit.labels.all())
        # Clear local outdated cache
        unit.translation.stats.clear()
        self.assertEqual(
            getattr(unit.translation.stats, f"label:{label.name}"),
            0,
        )

    def test_source_state(self):
        with override_settings(CREATE_GLOSSARIES=self.CREATE_GLOSSARIES):
            mono = Component.objects.create(
                name="Test2",
                slug="test2",
                project=self.project,
                repo="weblate://test/test",
                file_format="json",
                filemask="json-mono/*.json",
                template="json-mono/en.json",
            )
        # Translate single unit
        translation = mono.translation_set.get(language_code="cs")
        translation.unit_set.get(context="hello").translate(
            self.user, "Ahoj světe", STATE_TRANSLATED
        )
        self.assertEqual(translation.unit_set.filter(state=STATE_READONLY).count(), 0)
        self.assertEqual(translation.unit_set.filter(state=STATE_TRANSLATED).count(), 1)

        url = reverse("bulk-edit", kwargs={"path": mono.get_url_path()})

        # Mark all source strings as needing edit and that should turn all
        # translated strings read-only
        response = self.client.post(
            url, {"q": "language:en", "state": STATE_FUZZY}, follow=True
        )
        self.assertContains(response, "Bulk edit completed, 4 strings were updated.")
        self.assertEqual(translation.unit_set.filter(state=STATE_READONLY).count(), 4)
        self.assertEqual(translation.unit_set.filter(state=STATE_TRANSLATED).count(), 0)

        # Mark all source strings as needing edit and that should turn all
        # translated strings back to translated
        response = self.client.post(
            url, {"q": "language:en", "state": STATE_TRANSLATED}, follow=True
        )
        self.assertContains(response, "Bulk edit completed, 4 strings were updated.")
        self.assertEqual(translation.unit_set.filter(state=STATE_READONLY).count(), 0)
        self.assertEqual(translation.unit_set.filter(state=STATE_TRANSLATED).count(), 1)
