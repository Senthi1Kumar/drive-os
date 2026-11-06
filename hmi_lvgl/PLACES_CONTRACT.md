# Places, thumbnails and location source over /v1/ui (contract v1, additive)

All fields are optional; an HMI that ignores them keeps working.

- `hello.image_root`: absolute directory of the back-end's image cache. Every `img` path below is relative to it.
  The HMI loads `A:<image_root>/<path>` (LVGL POSIX file system, libjpeg decoder, decoded thumbnails in LVGL's image cache).
- `img` (160 px square JPEG) and `img_lg` (320 px) on: `shop.places[]`, `shop.menu[].items[]`, `shop.cart.lines[]` (only `img`),
  `shop.item` (`img_lg`), `home.places[]`. Empty or absent means no image: show the icon / initials.
- `shop.sample` and `shop.places[].sample` / `home.places[].sample`: the menu is a generated demo menu for a real place,
  not the business's. The UI must label it "Sample Only".
- `vehicle.source` (`gps` | `sim`) and `vehicle.source_text` ("Live GPS" | "Simulated . Palo Alto"): where the position comes from.
- `home.places[]`: up to 8 real places near the car, a few per everyday category (`id`, `name`, `category`, `distance_m`,
  `distance_text`, `orderable`, `sample`, `img`, `img_lg`). `open_place` / `drive_to` accept their ids.

Photos for items that have a real picture (the Los Gatos catalogue) appear after the back-end has downloaded and
downsized them; until then the fields are absent and the icon shows. Items without a photo get one of the generated
food images in `assets/food/` (see `assets/food/LICENSE.md`).

## Item variants (additive)

- `shop.item.groups[].options[]` may carry `img` and `img_lg` (relative paths, same rules as above). A brand option
  (group id `brand`) has them: the sheet photo `shop.item.img_lg` already follows the selected option, so the HMI
  only needs to redraw the sheet when the snapshot changes. Chips show `name` and, when non-empty, `delta`.
- Selection: command `set_option {"group": id, "option": id}` picks the option in a single-select group, toggles it
  in a multi-select group (`max_select` > 1, e.g. boba toppings, burger add-ons) and clears an optional group
  (`min_select` 0) when its selected option is tapped again. `add_item {"id", "qty"}` adds the current selection
  when no `options` are sent.
- `shop.cart.lines[].opts`: the chosen options as one short line, defaults left out except the brand
  ("Large · Iced · Oat · Vanilla", "Diet Coke"). Empty when there is nothing to show.
- Cart line `img` follows the chosen brand option as well.
- Places: `img` is always present for a place with a known category (hotels included) because the back-end falls back
  to a generated cover while a real photo is still downloading. A path is only sent when the file exists and is not
  empty; the HMI also checks the file before handing a path to LVGL (`src/thumb_path.c`).
- The generated cover of a place is assigned per list (`images/matcher.py: assign_covers`): the first eight places of
  `shop.places` or `home.places` never share a cover while the category's pool (eight or more scenes) has one left, and
  the assignment is cached per list so a repeated snapshot sends the same `img` for each place. A restaurant whose POI
  category or name hints at a cuisine (sushi, Thai, Indian, pizza, burger, Mexican, Mediterranean, ice cream, salad,
  chicken) gets that cuisine's cover first.
