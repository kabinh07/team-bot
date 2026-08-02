import os
import tempfile

import pytest
from fastapi.testclient import TestClient


@pytest.fixture()
def client():
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    os.environ["DATABASE_URL"] = f"sqlite:///{path}"

    import importlib

    import api
    import auth
    import models

    importlib.reload(models)
    importlib.reload(auth)
    importlib.reload(api)

    models.Base.metadata.drop_all(models.engine)
    models.Base.metadata.create_all(models.engine)

    with TestClient(api.app) as test_client:
        yield test_client

    models.Base.metadata.drop_all(models.engine)
    os.remove(path)


def test_change_password_updates_hashed_password(client):
    from auth import create_token, hash_password, verify_password
    from models import Department, ROLE_ENGINEER, User

    db = models.Session()
    department = Department(name="Ops", color="#0f6e5c")
    db.add(department)
    db.commit()
    db.refresh(department)

    user = User(
        username="tester",
        password_hash=hash_password("oldpass"),
        name="Tester",
        initials="TT",
        color="#0f6e5c",
        role=ROLE_ENGINEER,
        department_id=department.id,
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    db.close()

    token = create_token(user)
    response = client.post(
        "/api/auth/change-password",
        json={
            "currentPassword": "oldpass",
            "newPassword": "newpass123",
            "confirmPassword": "newpass123",
        },
        headers={"Authorization": f"Bearer {token}"},
    )

    assert response.status_code == 200
    assert response.json()["ok"] is True

    db = models.Session()
    updated_user = db.query(User).get(user.id)
    assert verify_password("newpass123", updated_user.password_hash)
    assert not verify_password("oldpass", updated_user.password_hash)
    db.close()
