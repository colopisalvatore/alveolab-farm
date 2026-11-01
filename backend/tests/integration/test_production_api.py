"""/production: recipes, stock, demand from orders, production plates and needs per colour."""

import pytest
from httpx import AsyncClient

API = "/api/v1/production"

FIOCCO = {
    "name": "Fiocco",
    "aliases": ["Pupazzo di neve"],
    "components": [
        {"key": "corpo", "library_file_id": 10, "plate": 1, "colour_hex": "#ffffff", "colour_name": "Bianco"},
        {"key": "sciarpa", "library_file_id": 10, "plate": 2, "colour_hex": "C12E1F", "colour_name": "Rosso"},
    ],
}


async def _recipe(client: AsyncClient, body: dict) -> dict:
    resp = await client.post(f"{API}/recipes", json=body)
    assert resp.status_code == 200, resp.text
    return resp.json()


async def _needs(client: AsyncClient) -> dict:
    resp = await client.get(f"{API}/needs")
    assert resp.status_code == 200, resp.text
    return {c["colour_name"]: c["total"] for c in resp.json()["colours"]}


class TestProduction:
    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_order_to_needs_to_plate_to_stock_to_shipping(self, async_client: AsyncClient):
        fiocco = await _recipe(async_client, FIOCCO)
        assert fiocco["components"][0]["colour_hex"] == "#FFFFFF"

        # An order line, sent twice (the sync runs every few minutes): one line.
        for _ in range(2):
            resp = await async_client.post(
                f"{API}/demand",
                json={
                    "recipe": "Fiocco",
                    "variant": "Classica",
                    "qty": 4,
                    "source": "shopify:deko",
                    "external_ref": "deko#1114:1",
                },
            )
            assert resp.status_code == 200, resp.text
        demand = (await async_client.get(f"{API}/demand")).json()
        assert len(demand) == 1 and demand[0]["recipe_id"] == fiocco["id"]
        assert await _needs(async_client) == {"Bianco": 4, "Rosso": 4}

        # A plate with 3 bodies is prepared: they count as coming.
        plate = (
            await async_client.post(
                f"{API}/plates",
                json={
                    "library_file_id": 99,
                    "printer_id": None,
                    "contents": [{"recipe_id": fiocco["id"], "component_key": "corpo", "sets": 3}],
                },
            )
        ).json()
        assert await _needs(async_client) == {"Bianco": 1, "Rosso": 4}
        assert [p["id"] for p in (await async_client.get(f"{API}/plates", params={"library_file_id": 99})).json()] == [
            plate["id"]
        ]

        # Printed (twice: counted once) -> 3 loose bodies.
        for _ in range(2):
            assert (await async_client.post(f"{API}/plates/{plate['id']}/printed")).json()["status"] == "printed"
        stock = {
            (s["recipe_id"], s["component_key"]): s["qty"] for s in (await async_client.get(f"{API}/stock")).json()
        }
        assert stock == {(fiocco["id"], "corpo"): 3}
        assert await _needs(async_client) == {"Bianco": 1, "Rosso": 4}

        # Assembling needs the scarves too.
        resp = await async_client.post(f"{API}/assemble", json={"recipe_id": fiocco["id"], "qty": 3})
        assert resp.status_code == 400 and "sciarpa" in resp.json()["detail"]
        await async_client.put(f"{API}/stock", json={"recipe_id": fiocco["id"], "component_key": "sciarpa", "qty": 4})
        assert (
            await async_client.post(f"{API}/assemble", json={"recipe_id": fiocco["id"], "qty": 3})
        ).status_code == 200
        assert await _needs(async_client) == {"Bianco": 1}

        # Shipped: the order is done and the finished products leave the stock.
        await async_client.post(f"{API}/demand/{demand[0]['id']}/close", json={"status": "done"})
        assert (await async_client.get(f"{API}/demand")).json() == []
        stock = {
            (s["recipe_id"], s["component_key"]): s["qty"] for s in (await async_client.get(f"{API}/stock")).json()
        }
        assert stock == {(fiocco["id"], "sciarpa"): 1}
        assert await _needs(async_client) == {}

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_failed_plate_is_owed_again(self, async_client: AsyncClient):
        fiocco = await _recipe(async_client, FIOCCO)
        await async_client.post(f"{API}/demand", json={"recipe_id": fiocco["id"], "qty": 2})
        plate = (
            await async_client.post(
                f"{API}/plates", json={"contents": [{"recipe_id": fiocco["id"], "component_key": "sciarpa", "sets": 2}]}
            )
        ).json()
        assert await _needs(async_client) == {"Bianco": 2}
        await async_client.post(f"{API}/plates/{plate['id']}/failed")
        assert await _needs(async_client) == {"Bianco": 2, "Rosso": 2}

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_mistakes_are_refused_with_the_reason(self, async_client: AsyncClient):
        fiocco = await _recipe(async_client, FIOCCO)
        assert (await async_client.post(f"{API}/recipes", json=FIOCCO)).status_code == 409
        bad = {**FIOCCO, "name": "Doppio", "components": [FIOCCO["components"][0], FIOCCO["components"][0]]}
        assert (await async_client.post(f"{API}/recipes", json=bad)).status_code == 422
        resp = await async_client.post(f"{API}/demand", json={"recipe": "Cometa", "qty": 1})
        assert resp.status_code == 422 and "Cometa" in resp.json()["detail"]
        resp = await async_client.post(
            f"{API}/plates", json={"contents": [{"recipe_id": fiocco["id"], "component_key": "naso", "sets": 1}]}
        )
        assert resp.status_code == 400
        assert (await async_client.post(f"{API}/stock/adjust", json={"recipe_id": 999, "delta": 1})).status_code == 404

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_variant_and_recipe_update(self, async_client: AsyncClient):
        fiocco = await _recipe(async_client, FIOCCO)
        ice = await _recipe(async_client, {**FIOCCO, "variant": "Ghiaccio", "aliases": []})
        resp = await async_client.post(f"{API}/demand", json={"recipe": "Fiocco Ghiaccio", "qty": 1})
        assert resp.json()["recipe_id"] == ice["id"]
        changed = {**FIOCCO, "components": FIOCCO["components"][:1]}
        assert len((await async_client.put(f"{API}/recipes/{fiocco['id']}", json=changed)).json()["components"]) == 1
