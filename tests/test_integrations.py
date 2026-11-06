"""Provider contracts against recorded-shape responses (no network): Mapbox, Google Places, Square, Stripe."""

from __future__ import annotations

import httpx
import pytest
import respx
from pydantic import SecretStr

from driveos.config import MapboxConfig, StripeConfig
from driveos.core.http import Http
from driveos.domain.models import GeoPoint
from driveos.domain.money import Money
from driveos.enrichment.google_places import GooglePlaces
from driveos.merchants.square import parse_square_menu
from driveos.nav.geo import encode_polyline
from driveos.nav.mapbox import MapboxClient
from driveos.payments.stripe_provider import StripePayments

A = GeoPoint(lat=37.5347, lon=-121.9949)
B = GeoPoint(lat=37.5410, lon=-121.9949)
C = GeoPoint(lat=37.5410, lon=-121.9800)

DIRECTIONS = {
    "code": "Ok",
    "routes": [{
        "geometry": encode_polyline([A, B, C], 6), "distance": 2010.4, "duration": 241.0,
        "legs": [{"summary": "Fremont Blvd, Mowry Ave", "steps": [
            {"name": "Fremont Blvd", "distance": 700, "duration": 80,
             "maneuver": {"type": "depart", "location": [A.lon, A.lat], "instruction": "Drive north"}},
            {"name": "Mowry Ave", "distance": 1310, "duration": 161,
             "maneuver": {"type": "turn", "modifier": "right", "location": [B.lon, B.lat],
                          "instruction": "Turn right onto Mowry Ave"},
             "bannerInstructions": [{"primary": {"text": "Mowry Ave"}}]},
            {"name": "", "distance": 0, "duration": 0,
             "maneuver": {"type": "arrive", "location": [C.lon, C.lat], "instruction": "You have arrived"}},
        ]}],
    }],
    "waypoints": [{"name": "Fremont Blvd"}, {"name": "Work"}],
}


@pytest.fixture
async def http():
    h = Http()
    yield h
    await h.client.aclose()


@respx.mock
async def test_mapbox_directions(http):
    route_ = respx.get(url__regex=r"https://api\.mapbox\.com/directions/v5/mapbox/driving-traffic/.*").mock(
        return_value=httpx.Response(200, json=DIRECTIONS))
    mb = MapboxClient(MapboxConfig(token=SecretStr("pk.test")), http)
    r = await mb.directions([A, C], names=["Here", "Work"])
    q = route_.calls.last.request.url.params
    assert q["geometries"] == "polyline6" and q["steps"] == "true" and q["access_token"] == "pk.test"
    assert len(r.steps) == 3 and r.steps[1].maneuver.modifier == "right" and r.steps[1].banner == "Mowry Ave"
    assert r.steps[1].geometry_index == 1
    assert r.destination_name == "Work"
    assert r.cumulative_m and abs(r.cumulative_m[-1] - 2010) < 40


@respx.mock
async def test_mapbox_category_along_route(http):
    from driveos.nav.mapbox import parse_directions

    respx.get(url__regex=r".*/search/searchbox/v1/category/coffee.*").mock(return_value=httpx.Response(200, json={
        "type": "FeatureCollection", "features": [{
            "type": "Feature", "geometry": {"type": "Point", "coordinates": [-121.99, 37.54]},
            "properties": {"mapbox_id": "dXJu", "name": "Halfmoon Coffee", "full_address": "1 Mowry Ave",
                           "poi_category_ids": ["coffee", "cafe"], "eta": 2.1, "distance": 640}}]}))
    mb = MapboxClient(MapboxConfig(token=SecretStr("pk.test")), http)
    res = await mb.category_along_route("coffee", parse_directions(DIRECTIONS), origin=A)
    sent = respx.calls.last.request.url.params
    assert sent["sar_type"] == "isochrone" and sent["route_geometry"] == "polyline6"
    assert res[0]["name"] == "Halfmoon Coffee" and res[0]["location"].lat == 37.54


@respx.mock
async def test_mapbox_error_is_provider_unavailable(http):
    from driveos.domain.errors import ProviderUnavailable

    respx.get(url__regex=r".*/directions/.*").mock(return_value=httpx.Response(422, json={"code": "InvalidInput"}))
    with pytest.raises(ProviderUnavailable):
        await MapboxClient(MapboxConfig(token=SecretStr("pk.test")), http).directions([A, C])


@respx.mock
async def test_google_places_along_route(http):
    call = respx.post("https://places.googleapis.com/v1/places:searchText").mock(return_value=httpx.Response(200, json={
        "places": [{
            "id": "ChIJ1", "displayName": {"text": "Saffron Street Kitchen"}, "formattedAddress": "2 Paseo Padre",
            "location": {"latitude": 37.55, "longitude": -121.98}, "rating": 4.7, "userRatingCount": 812,
            "priceLevel": "PRICE_LEVEL_MODERATE", "currentOpeningHours": {"openNow": True},
            "primaryTypeDisplayName": {"text": "Mediterranean restaurant"},
            "photos": [{"name": "places/ChIJ1/photos/abc", "authorAttributions": [{"displayName": "A. Lee"}]}],
        }]}))
    gp = GooglePlaces("key", http)
    res = await gp.along_route("food", [A, B, C], origin=A)
    req = call.calls.last.request
    assert req.headers["X-Goog-Api-Key"] == "key" and "places.photos" in req.headers["X-Goog-FieldMask"]
    body = req.read().decode()
    assert "searchAlongRouteParameters" in body and '"includedType":"restaurant"' in body.replace(" ", "")
    p = res[0]
    assert p["price_level"] == 2 and p["photo_name"].endswith("/abc") and p["photo_attribution"] == "A. Lee"


def test_square_menu_parse():
    items = [{
        "type": "ITEM", "id": "ITEM_LATTE",
        "item_data": {
            "name": "Latte", "description_plaintext": "Double shot", "image_ids": ["IMG1"],
            "categories": [{"id": "CAT1"}],
            "variations": [
                {"id": "V_SM", "item_variation_data": {"name": "Small", "price_money": {"amount": 475, "currency": "USD"}}},
                {"id": "V_LG", "item_variation_data": {"name": "Large", "price_money": {"amount": 550, "currency": "USD"}}},
            ],
            "modifier_list_info": [{"modifier_list_id": "ML_MILK", "min_selected_modifiers": 0,
                                    "max_selected_modifiers": 1}],
        }}]
    related = {
        "IMG1": {"type": "IMAGE", "id": "IMG1", "image_data": {"url": "https://items-images/latte.jpg"}},
        "CAT1": {"type": "CATEGORY", "id": "CAT1", "category_data": {"name": "Espresso"}},
        "ML_MILK": {"type": "MODIFIER_LIST", "id": "ML_MILK", "modifier_list_data": {"name": "Milk", "modifiers": [
            {"id": "M_OAT", "modifier_data": {"name": "Oat", "price_money": {"amount": 75, "currency": "USD"}}},
            {"id": "M_WHOLE", "modifier_data": {"name": "Whole", "on_by_default": True}}]}},
    }
    menu = parse_square_menu("LOC1", items, related)
    latte = menu.item("ITEM_LATTE")
    assert latte.base_price.amount == 475 and latte.image_url.endswith("latte.jpg") and menu.categories == ["Espresso"]
    size, milk = latte.modifier_groups
    assert [o.price_delta.amount for o in size.options] == [0, 75]
    assert milk.max_select == 1 and [o.default for o in milk.options] == [False, True]


@respx.mock
async def test_stripe_manual_capture_flow(http):
    from driveos.config import _default_payers

    pi = {"id": "pi_1", "status": "requires_capture", "amount": 689, "currency": "usd"}
    create = respx.post("https://api.stripe.com/v1/payment_intents").mock(return_value=httpx.Response(200, json=pi))
    respx.post("https://api.stripe.com/v1/payment_intents/pi_1/capture").mock(
        return_value=httpx.Response(200, json={**pi, "status": "succeeded", "amount_received": 689}))
    sp = StripePayments(StripeConfig(secret_key=SecretStr("sk_test_x"), customer_id="cus_1"), http)
    auth = await sp.authorize(Money(amount=689), _default_payers()[0], idempotency_key="tx1:authorize",
                              description="Halfmoon · DriveOS", metadata={"tx_id": "tx1"})
    req = create.calls.last.request
    form = dict(httpx.QueryParams(req.read().decode()))
    assert form["capture_method"] == "manual" and form["confirm"] == "true" and form["off_session"] == "true"
    assert form["metadata[tx_id]"] == "tx1" and req.headers["Idempotency-Key"] == "tx1:authorize"
    assert req.headers["Stripe-Version"] == "2026-09-30.endive"
    assert auth.status == "authorized" and auth.auth_id == "pi_1"
    cap = await sp.capture(auth, idempotency_key="tx1:capture")
    assert cap.status == "captured"


@respx.mock
async def test_stripe_decline_maps_reason(http):
    from driveos.config import _default_payers

    respx.post("https://api.stripe.com/v1/payment_intents").mock(return_value=httpx.Response(402, json={
        "error": {"type": "card_error", "code": "card_declined", "decline_code": "insufficient_funds",
                  "payment_intent": {"id": "pi_2", "status": "requires_payment_method"}}}))
    sp = StripePayments(StripeConfig(secret_key=SecretStr("sk_test_x")), http)
    auth = await sp.authorize(Money(amount=689), _default_payers()[0], idempotency_key="k", description="d",
                              metadata={})
    assert auth.status == "declined" and auth.decline_reason == "insufficient_funds"


# ---------------------------------------------------------------- DriveOS additions
@respx.mock
async def test_mapbox_pois_resolve_category_and_are_navigate_only(http):
    from driveos.merchants.mapbox_pois import MapboxPoiProvider

    respx.get(url__regex=r".*/searchbox/v1/list/category.*").mock(return_value=httpx.Response(200, json={
        "listItems": [{"canonical_id": "gas_station"}, {"canonical_id": "bar"}]}))
    cat = respx.get(url__regex=r".*/searchbox/v1/category/gas_station.*").mock(return_value=httpx.Response(200, json={
        "features": [{"geometry": {"coordinates": [-121.97, 37.24]},
                      "properties": {"mapbox_id": "m1", "name": "Shell", "full_address": "1 Los Gatos Blvd"}}]}))
    prov = MapboxPoiProvider(MapboxClient(MapboxConfig(token=SecretStr("pk.test")), http))
    res = await prov.search(near=A, route=None, category="gas", query=None, limit=5)
    assert cat.called and "bbox" in cat.calls.last.request.url.params
    assert res[0].name == "Shell" and res[0].extra["orderable"] is False


@respx.mock
async def test_mapbox_geocode(http):
    respx.get(url__regex=r".*/search/geocode/v6/forward.*").mock(return_value=httpx.Response(200, json={
        "features": [{"geometry": {"coordinates": [-121.9456, 37.2525]}}]}))
    p = await MapboxClient(MapboxConfig(token=SecretStr("pk.test")), http).geocode("2425 Samaritan Dr")
    assert (p.lat, p.lon) == (37.2525, -121.9456)


@respx.mock
async def test_twilio_sms_delivery(http):
    from driveos.wallet.auth import OtpService, TwilioSms

    route_ = respx.post("https://api.twilio.com/2010-04-01/Accounts/AC1/Messages.json").mock(
        return_value=httpx.Response(201, json={"sid": "SM1"}))
    svc = OtpService(TwilioSms("AC1", "tok", "+15550001111", http))
    ch = await svc.issue("+14085550117", "$40.00 at Summit Fuel")
    form = dict(httpx.QueryParams(route_.calls.last.request.read().decode()))
    assert form["To"] == "+14085550117" and form["From"] == "+15550001111"
    code = form["Body"][:6]
    assert code.isdigit() and svc.verify(ch.challenge_id, code).success
    assert route_.calls.last.request.headers["Authorization"].startswith("Basic ")


@respx.mock
async def test_agent_tool_loop_over_openrouter(settings, tmp_path):
    """The model's tool calls drive the same actions as voice/touch; its reply is spoken."""
    from driveos.agent.harness.brain import HarnessBrain
    from driveos.container import build_services

    from .agent_util import URL, bodies, make_settings, script, sse

    make_settings(settings, tmp_path)
    svc = build_services(settings)
    assert isinstance(svc.nova.brain, HarnessBrain)
    api = respx.post(URL).mock(side_effect=script(
        sse(tools=[("find_places", '{"category":"bakery","query":"bagel"}')]),
        sse("The Posh Bagel is closest. Want to order?")))
    await svc.nova.handle_text("I have ten minutes before my meeting, anything quick around here?")
    body = bodies(api)[0]
    assert body["tools"] and any(t["function"]["name"] == "find_places" for t in body["tools"])
    assert svc.nova.shop.view == "places" and svc.nova.last_spoken.startswith("The Posh Bagel")
    await svc.aclose()


@respx.mock
async def test_agent_unreachable_gives_a_notice_then_rules(settings, tmp_path):
    from driveos.container import build_services

    from .agent_util import URL, make_settings

    make_settings(settings, tmp_path)
    svc = build_services(settings)
    respx.post(URL).mock(return_value=httpx.Response(529, json={}))
    await svc.nova.handle_text("tell me something nice")
    assert "offline" in svc.nova.last_spoken
    await svc.nova.handle_text("tell me something else")
    assert "What do you need" in svc.nova.last_spoken
    await svc.aclose()
