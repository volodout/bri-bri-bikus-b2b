# bri-bri-bikus-b2b — NeoMarket B2C Catalog + Favorites

Сервис каталога B2C для NeoMarket. Реализует **US-CAT-01: каталог с фильтрами и
фасетами** ([канон-flow](neomarket-canon/flows/b2c-catalog-flows.md#b2c-1-catalog-filters))
и **US-CART-01: избранное покупателя**
([канон-flow](neomarket-canon/flows/b2c-cart-flows.md#b2c-6-favorites)).

B2C **не хранит товары** — все запросы каталога проксируются в B2B по HTTP с
заголовком `X-Service-Key`. B2B применяет условие видимости
(`status = MODERATED AND deleted = false AND active_quantity > 0`).
Для избранного B2C хранит только связь `user_id + product_id + added_at`,
а данные товаров обогащает batch-запросом в B2B.

## Эндпоинты

| Метод | Путь | Назначение |
|-------|------|------------|
| GET | `/api/v1/products` | Список товаров: фильтры (`filters[brand]=…`), сортировка, пагинация, поиск |
| GET | `/api/v1/catalog/facets` | Подсчёты по каждому значению фильтра для текущей выборки |
| GET | `/api/v1/favorites` | Список избранного текущего пользователя с B2B-обогащением |
| POST | `/api/v1/favorites/{product_id}` | Идемпотентное добавление товара в избранное |
| DELETE | `/api/v1/favorites/{product_id}` | Идемпотентное удаление товара из избранного |

Допустимые значения `sort`: `rating`, `popularity`, `price_asc`, `price_desc`,
`date_desc`, `discount_desc`. Невалидное — `400 INVALID_REQUEST` с перечислением
допустимых.

## Запуск

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements-dev.txt

# конфиг через env
export B2B_BASE_URL=http://localhost:8001
export B2B_SERVICE_KEY=dev-service-key
export DATABASE_URL=postgresql://neomarket:neomarket@localhost:5432/neomarket

alembic upgrade head
uvicorn app.main:app --reload
```

## Тесты

```bash
pytest -v
```

GitHub Actions (`.github/workflows/tests.yml`) прогоняет именованные тесты из DoD:

- `catalog_returns_filtered_sorted_products`
- `facets_return_counts_per_filter_value`
- `invalid_sort_returns_400`
- `b2b_unavailable_returns_502`
- `add_to_favorites_returns_201`
- `get_favorites_enriched_from_b2b`
- `repeat_add_returns_200_not_duplicate`
- `blocked_product_excluded_from_list`
- `user_id_from_query_is_ignored`

## Структура

```
app/
├── main.py             # FastAPI app, регистрация роутеров и хендлеров ошибок
├── config.py           # B2B_BASE_URL, B2B_SERVICE_KEY (env)
├── b2b_client.py       # httpx async client; маппинг upstream-ошибок → 502/400/404
├── errors.py           # ErrorResponse {code, message}, исключения каталога
├── favorites.py        # use-case/repository model избранного
├── auth.py             # извлечение user_id из JWT sub
├── query_parsing.py    # валидация sort, пагинации, UUID, search; парсинг filters[]
└── routes/
    ├── products.py
    ├── facets.py
    └── favorites.py
tests/
├── conftest.py         # фикстура B2B на httpx.MockTransport
├── test_catalog.py
└── test_favorites.py
docs/adr/
└── 0001-facets-computation.md
```

## Канон

Канон-flows и OpenAPI приложены в `neomarket-canon/` как git subtree
(`flows/b2c-catalog-flows.md`, `flows/b2c-cart-flows.md`,
`apis/b2c/catalog/openapi.yaml`, `apis/b2c/cart/openapi.yaml`). ADR — в
[`docs/adr/0001-facets-computation.md`](docs/adr/0001-facets-computation.md).
