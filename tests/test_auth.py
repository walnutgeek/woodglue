import time
from datetime import datetime, timedelta

from woodglue.auth import EntityPrivates, Grant, IdentityKey, Principal
from woodglue.misc import tabula_rasa_dir

ep_dirs = [tabula_rasa_dir(f"build/tests/auth/EntityPrivates/{i}") for i in range(1, 3)]


def test_EntityPrivates_and_Principal_roundtrip():
    assert not ep_dirs[0].is_dir()
    ep = EntityPrivates(ep_dirs[0])
    test_signature = ep.sign("test")

    assert ep.validate_files()

    assert ep_dirs[0].is_dir()
    ep = EntityPrivates(ep_dirs[0])

    assert ep.verify("test", test_signature)

    assert not ep_dirs[1].is_dir()
    ep2 = EntityPrivates(ep_dirs[1])

    assert not ep2.verify("test", test_signature)

    p = Principal.from_identity(ep)
    assert p.get_identity().verify("test", test_signature)

    assert (
        Principal.model_validate_json(p.model_dump_json())
        .get_identity()
        .verify("test", test_signature)
    )

    assert p.hashkey == p.get_identity().hashkey()

    assert IdentityKey(Principal.model_validate_json(p.model_dump_json()).get_ed25519_pub()).verify(
        "test", test_signature
    )


def test_Grant():
    ep1, ep2 = map(EntityPrivates, ep_dirs)
    g1, g2 = (
        Grant(principal=Principal.from_identity(ep1)),
        Grant(
            principal=Principal.from_identity(ep2),
            expires_at=datetime.now() + timedelta(seconds=1),
        ),
    )
    tokens = [g1.seal_token(ep1), g2.seal_token(ep2)]
    velifications = [
        Grant.verify_token(tokens[0], ep1),
        Grant.verify_token(tokens[0], ep2),
        Grant.verify_token(tokens[1], Principal.from_identity(ep2).get_identity()),
        Grant.verify_token(tokens[1], Principal.from_identity(ep1).get_identity()),
    ]
    assert [(v, g is not None) for v, g in velifications] == [
        (True, True),
        (False, False),
        (True, True),
        (False, False),
    ]
    vgrants = [g for _, g in velifications if g is not None]
    assert vgrants == [g1, g2]
    assert [g.is_expired() for g in vgrants] == [False, False]
    time.sleep(1.1)
    assert [g.is_expired() for g in vgrants] == [False, True]
