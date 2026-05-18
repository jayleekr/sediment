from lab_lib.auth import mint_token, decode_token


def test_mint_decode_roundtrip():
    token = mint_token(member_id="m-1", tenant_id="t-1", role="admin", email="a@b.test")
    identity = decode_token(token)
    assert identity.member_id == "m-1"
    assert identity.tenant_id == "t-1"
    assert identity.role == "admin"
    assert identity.email == "a@b.test"
