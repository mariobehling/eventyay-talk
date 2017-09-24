import urllib
from contextlib import suppress

import pytz
from django.conf import settings
from django.core.exceptions import PermissionDenied
from django.shortcuts import redirect, reverse
from django.urls import resolve
from django.utils import timezone, translation
from django.utils.translation.trans_real import (
    get_supported_language_variant, language_code_re, parse_accept_lang_header,
)

from pretalx.event.models import Event
from pretalx.person.models import EventPermission


class EventPermissionMiddleware:
    UNAUTHENTICATED = (
        'invitation.view',
        'login',
    )

    def __init__(self, get_response):
        self.get_response = get_response

    def _set_orga_events(self, request):
        if not request.user.is_anonymous:
            if request.user.is_superuser:
                request.orga_events = Event.objects.all()
            else:
                request.orga_events = Event.objects.filter(
                    permissions__user=request.user,
                    permissions__is_orga=True,
                )

    def __call__(self, request):
        url = resolve(request.path_info)

        event_slug = url.kwargs.get('event')
        if event_slug:
            try:
                request.event = Event.objects.get(slug__iexact=event_slug)
            except Event.DoesNotExist:
                request.event = None

            if hasattr(request, 'event') and request.event:
                if not request.user.is_anonymous:
                    request.is_orga = request.user.is_superuser or EventPermission.objects.filter(
                        user=request.user,
                        event=request.event,
                        is_orga=True
                    ).exists()
                timezone.activate(pytz.timezone(request.event.timezone))

        self._set_orga_events(request)

        if 'orga' in url.namespaces:
            if request.user.is_anonymous and url.url_name not in self.UNAUTHENTICATED:
                redirect_path = urllib.parse.quote(request.path)
                return redirect(reverse('orga:login') + f'?next={redirect_path}')
            if hasattr(request, 'event') and not request.is_orga:
                raise PermissionDenied()

            self._select_locale(request)

        return self.get_response(request)

    def _select_locale(self, request):
        supported = request.event.locales if hasattr(request, 'event') else settings.LANGUAGES
        language = (
            self._language_from_user(request, supported)
            or self._language_from_browser(request, supported)
        )
        if hasattr(request, 'event'):
            language = language or request.event.locale

        translation.activate(language)
        request.LANGUAGE_CODE = translation.get_language()

        with suppress(pytz.UnknownTimeZoneError):
            if request.user.is_authenticated:
                tzname = request.user.timezone
            elif hasattr(request, 'event'):
                tzname = request.event.timezone
            else:
                tzname = settings.TIME_ZONE
            timezone.activate(pytz.timezone(tzname))
            request.timezone = tzname

    def _language_from_browser(self, request, supported):
        accept_value = request.META.get('HTTP_ACCEPT_LANGUAGE', '')
        for accept_lang, unused in parse_accept_lang_header(accept_value):
            if accept_lang == '*':
                break

            if not language_code_re.search(accept_lang):
                continue

            try:
                val = get_supported_language_variant(accept_lang)
                if val and val in supported:
                    return val
            except LookupError:
                continue

    def _language_from_cookie(self, request, supported):
        cookie_value = request.COOKIES.get(settings.LANGUAGE_COOKIE_NAME)
        with suppress(LookupError):
            cookie_value = get_supported_language_variant(cookie_value)
            if cookie_value and cookie_value in supported:
                return cookie_value

    def _language_from_user(self, request, supported):
        if request.user.is_authenticated:
            with suppress(LookupError):
                value = get_supported_language_variant(request.user.locale)
                if value and value in supported:
                    return value
