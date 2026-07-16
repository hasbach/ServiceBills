from tests.conftest import make_tenant


def test_users_isolation(client):
    a = make_tenant(client, "Biz A", "a_admin")
    b = make_tenant(client, "Biz B", "b_admin")

    # Tenant A's admin creates an employee in tenant A.
    r = client.post("/api/users", headers=a,
                    json={"username": "a_emp", "password": "pw", "role": "employee"})
    assert r.status_code == 201

    a_users = client.get("/api/users", headers=a).get_json()
    b_users = client.get("/api/users", headers=b).get_json()
    assert {u["username"] for u in a_users} == {"a_admin", "a_emp"}
    assert {u["username"] for u in b_users} == {"b_admin"}

    # Tenant B cannot modify or delete tenant A's user.
    a_emp_id = next(u["id"] for u in a_users if u["username"] == "a_emp")
    assert client.put(f"/api/users/{a_emp_id}", headers=b, json={"role": "admin"}).status_code == 404
    assert client.delete(f"/api/users/{a_emp_id}", headers=b).status_code == 404
