# Copyright © Michal Čihař <michal@weblate.org>
#
# SPDX-License-Identifier: GPL-3.0-or-later

import os

from django.conf import settings
from django.core.cache import cache
from django.template.loader import render_to_string
from django.urls import reverse
from django.utils.translation import gettext_lazy

from weblate.addons.models import ADDONS
from weblate.trans.models import Change
from weblate.utils.docs import get_doc_url

GUIDELINES = []


def register(cls):
    GUIDELINES.append(cls)
    return cls


class Guideline:
    description = ""
    group = False
    url = ""
    anchor = ""

    def __init__(self, component):
        self.component = component
        self.passed = self.is_passing()

    def is_passing(self):
        raise NotImplementedError

    def is_relevant(self):
        return True

    def get_url(self):
        url = reverse(self.url, kwargs={"path": self.component.get_url_path()})
        if self.anchor:
            url = f"{url}#{self.anchor}"
        return url

    def get_doc_url(self, user=None):
        return ""


class Group(Guideline):
    group = True

    def is_passing(self):
        # Not used
        return False

    def get_url(self):
        # Not used
        return ""


@register
class VCSGroup(Group):
    description = gettext_lazy("Version control integration")

    def get_doc_url(self, user=None):
        return get_doc_url("vcs", user=user)


@register
class HookGuideline(Guideline):
    description = gettext_lazy(
        "Configure repository hooks for automated flow of updates to Weblate."
    )
    url = "settings"
    anchor = "vcs"

    def is_passing(self):
        return self.component.change_set.filter(action=Change.ACTION_HOOK).exists()

    def is_relevant(self):
        return not self.component.is_repo_link

    def get_url(self):
        return self.get_doc_url()

    def get_doc_url(self, user=None):
        return get_doc_url("admin/continuous", "update-vcs", user=user)


@register
class PushGuideline(Guideline):
    description = gettext_lazy(
        "Configure push URL for automated flow of translations from Weblate."
    )
    url = "settings"
    anchor = "vcs"

    def is_passing(self):
        return self.component.can_push()

    def get_doc_url(self, user=None):
        return get_doc_url("admin/continuous", "push-changes", user=user)


@register
class CommunityGroup(Group):
    description = gettext_lazy("Building community")

    def get_doc_url(self, user=None):
        return get_doc_url("devel/community", user=user)


@register
class InstructionsGuideline(Guideline):
    description = gettext_lazy(
        "Define translation instructions to give translators a guideline."
    )

    def is_passing(self):
        return bool(self.component.project.instructions)

    def get_url(self):
        return reverse(
            "settings", kwargs={"path": self.component.project.get_url_path()}
        )

    def get_doc_url(self, user=None):
        return get_doc_url("admin/projects", "project", user=user)


@register
class LicenseGuideline(Guideline):
    description = gettext_lazy(
        "Make your translations available under a libre license."
    )
    url = "settings"
    anchor = "basic"

    def is_relevant(self):
        return settings.LICENSE_REQUIRED

    def is_passing(self):
        return self.component.libre_license

    def get_doc_url(self, user=None):
        return "https://choosealicense.com/"


@register
class AlertGuideline(Guideline):
    description = gettext_lazy("Fix this component to clear its alerts.")
    url = "show"
    anchor = "alerts"

    def is_passing(self):
        return not self.component.all_active_alerts

    def get_doc_url(self, user=None):
        return get_doc_url("devel/alerts", user=user)


@register
class ContextGroup(Group):
    description = gettext_lazy("Provide context to the translators")

    def get_doc_url(self, user=None):
        return get_doc_url("admin/translating", "additional", user=user)


@register
class ScreenshotGuideline(Guideline):
    description = gettext_lazy("Add screenshots to show where strings are being used.")
    url = "screenshots"

    def is_passing(self):
        from weblate.screenshots.models import Screenshot

        return Screenshot.objects.filter(translation__component=self.component).exists()

    def get_doc_url(self, user=None):
        return get_doc_url("admin/translating", "screenshots", user=user)


@register
class FlagsGuideline(Guideline):
    description = gettext_lazy(
        "Use flags to indicate special strings in your translation."
    )
    url = "settings"
    anchor = "show"

    def is_passing(self):
        return (
            bool(self.component.check_flags)
            or self.component.source_translation.unit_set.exclude(
                extra_flags=""
            ).exists()
        )

    def get_doc_url(self, user=None):
        return get_doc_url("admin/checks", "custom-checks", user=user)


@register
class SafeHTMLGuideline(Guideline):
    description = gettext_lazy(
        "Add safe-html flag to avoid dangerous HTML from translators."
    )
    url = "settings"
    anchor = "show"

    def is_relevant(self):
        cache_key = f"guide:safe-html:{self.component.id}"
        result = cache.get(cache_key)
        if result is not None:
            return result
        result = self.component.source_translation.unit_set.filter(
            source__contains="<a "
        ).exists()
        cache.set(cache_key, result, 86400)
        return result

    def is_passing(self):
        return (
            "safe-html" in self.component.check_flags
            or self.component.source_translation.unit_set.filter(
                extra_flags__contains="safe-html"
            ).exists()
        )

    def get_doc_url(self, user=None):
        return get_doc_url("user/checks", "check-safe-html", user=user)


@register
class AddonsGroup(Group):
    description = gettext_lazy("Workflow customization")

    def get_doc_url(self, user=None):
        return get_doc_url("admin/addons", user=user)


class AddonGuideline(Guideline):
    addon = ""
    url = "addons"

    def is_passing(self):
        return self.addon in self.component.addons_cache["__names__"]

    def is_relevant(self):
        if self.addon not in ADDONS:
            return False
        addon = ADDONS[self.addon]
        return addon.can_install(self.component, None)

    def get_doc_url(self, user=None):
        return get_doc_url(
            "admin/addons", ADDONS[self.addon].get_doc_anchor(), user=user
        )

    @property
    def description(self):
        return render_to_string(
            "trans/guide/addon.html",
            {
                "name": ADDONS[self.addon].verbose,
                "description": ADDONS[self.addon].description,
            },
        )


@register
class LanguageConsistencyGuideline(AddonGuideline):
    addon = "weblate.consistency.languages"

    def is_relevant(self):
        if self.component.project.component_set.exclude(is_glossary=True).count() <= 1:
            return False
        return super().is_relevant()


@register
class LinguasGuideline(AddonGuideline):
    addon = "weblate.gettext.linguas"


@register
class ConfigureGuideline(AddonGuideline):
    addon = "weblate.gettext.configure"


@register
class CleanupGuideline(AddonGuideline):
    addon = "weblate.cleanup.generic"


@register
class GenerateMoGuideline(AddonGuideline):
    addon = "weblate.gettext.mo"

    def is_relevant(self):
        if not super().is_relevant():
            return False
        component = self.component
        translations = component.translation_set.exclude(
            pk=component.source_translation.id
        )
        try:
            translation = translations[0]
        except IndexError:
            return False
        if not translation.filename.endswith(".po"):
            return False
        mofilename = translation.get_filename()[:-3] + ".mo"
        return os.path.exists(mofilename)
