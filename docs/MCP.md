# Merchant MCP contract

DriveOS talks to merchants through MCP servers. The vehicle is the MCP **client**
(`driveos.merchants.mcp_provider`); any number of servers can be listed under `[[merchants.mcp_servers]]`,
over stdio (`command` + `args`) or streamable HTTP (`url` + `headers`). Results from all sources are
merged and de-duplicated in priority order.

The bundled server (`driveos mcp-merchant`, `python -m driveos.mcp_server`) implements the contract with
two backends: the demo kitchen (orderable, `DRIVEOS_MCP_DEMO=1`) and Google Places (photos, ratings and
price levels, `GOOGLE_PLACES_API_KEY`; these places are `orderable: false` and navigate-only).

A partner integration (an ordering platform, a POS, a chain's own API) implements the same seven tools.
Every tool returns structured content; failures return `{"error": {"code", "message"}}` rather than raising.

| Tool | Arguments | Returns |
|---|---|---|
| `search_restaurants` | `latitude, longitude, category, query?, route_polyline6?, limit` | `{places: [Place]}`; each place has `merchant_id, name, location{lat,lon}, rating, price_level 1-4, photo_url, orderable, pickup_modes, prep_minutes` |
| `get_menu` | `merchant_id` | `{menu: {merchant_id, currency, categories, items: [{id, name, description, base_price{amount,currency}, image_url, modifier_groups}]}}` |
| `quote_order` | `merchant_id, lines: [{item_id, quantity, selections{group_id: [option_id]}}]` | `{quote: {quote_id, lines, subtotal, tax, fees, total, prep_minutes, expires_at}}` — binding until `expires_at` |
| `place_order` | `merchant_id, lines, expected_total, currency, payment_reference, payment_status, customer_name, idempotency_key, pickup_eta?` | `{order: {order_id, pickup_code, status, ready_at, pickup_spot, pickup_instructions}}`; must re-price and refuse with `quote_mismatch` if the total differs |
| `get_order_updates` | `order_id, cursor` | `{updates: [{status, ready_at, message, pickup_spot, at}], cursor}`; statuses `placed accepted preparing ready picked_up rejected canceled` |
| `notify_arrival` | `order_id` | `{ok: true}` |
| `cancel_order` | `order_id` | `{ok: bool}` — false once the kitchen can no longer cancel |

Rules a server must keep:

* `place_order` is idempotent on `idempotency_key`: a retry returns the same order.
* Money is always integer minor units with an ISO currency.
* The server never charges the customer. The vehicle authorises the payment (Stripe hold or Square payment)
  before calling `place_order` and passes the reference; the vehicle captures on `accepted` and voids on
  `rejected`/`canceled`.
* Images are absolute HTTPS URLs; attribution text, when the source requires it, goes in `photo_attribution`.
