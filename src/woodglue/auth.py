import base64
import hashlib
from datetime import datetime
from pathlib import Path

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey, Ed25519PublicKey
from pydantic import BaseModel, Field
from typing_extensions import override

from woodglue import GRef, JsonBase, encode_base64, ensure_bytes


class IdentityTrait:
    def get_ed25519_pub(self) -> Ed25519PublicKey:
        raise NotImplementedError

    @property
    def pubkey(self) -> str:
        return encode_base64(self.get_ed25519_pub().public_bytes_raw())

    def compute_hashkey(self) -> str:
        return hashlib.sha256(self.get_ed25519_pub().public_bytes_raw()).hexdigest()

    def verify(self, data: str | bytes, signature: str | bytes) -> bool:
        try:
            self.get_ed25519_pub().verify(ensure_bytes(signature), ensure_bytes(data))
        except InvalidSignature:
            return False
        return True


class IdentityKey(IdentityTrait):
    _ed25519_pub: Ed25519PublicKey
    _hashkey: str | None

    def hashkey(self, length: int = 0) -> str:
        if self._hashkey is None:
            self._hashkey = self.compute_hashkey()
        return self._hashkey[:length] if length > 0 else self._hashkey

    def __init__(self, pubkey: str | bytes | Ed25519PublicKey) -> None:
        self._hashkey = None
        if isinstance(pubkey, Ed25519PublicKey):
            self._ed25519_pub = pubkey
        else:
            self._ed25519_pub = Ed25519PublicKey.from_public_bytes(ensure_bytes(pubkey))

    @override
    def get_ed25519_pub(self) -> Ed25519PublicKey:
        return self._ed25519_pub


class SignerTrait:
    def get_ed25519(self) -> Ed25519PrivateKey:
        raise NotImplementedError

    def sign(self, data: str | bytes) -> bytes:
        return self.get_ed25519().sign(ensure_bytes(data))


class EntityPrivates(IdentityTrait, SignerTrait):
    priv_file: Path
    pub_file: Path

    _ed25519: Ed25519PrivateKey
    _ed25519_pub: Ed25519PublicKey

    def __init__(self, keys_dir: Path) -> None:
        self.priv_file = keys_dir / "x25519.priv"
        self.pub_file = keys_dir / "x25519.pub"
        if not keys_dir.exists():
            keys_dir.mkdir(parents=True, exist_ok=True)
            keys_dir.chmod(0o700)
            self._ed25519 = Ed25519PrivateKey.generate()
            self._ed25519_pub = self._ed25519.public_key()
            private_bytes = self._ed25519.private_bytes_raw()
            public_bytes = self._ed25519_pub.public_bytes_raw()
            self.pub_file.write_bytes(public_bytes)
            self.priv_file.write_bytes(private_bytes)
            self.priv_file.chmod(0o600)
        else:
            assert self.priv_file.exists()
            assert self.pub_file.exists()
            private_bytes = self.priv_file.read_bytes()
            public_bytes = self.pub_file.read_bytes()
            self._ed25519 = Ed25519PrivateKey.from_private_bytes(private_bytes)
            self._ed25519_pub = self._ed25519.public_key()

    @override
    def get_ed25519_pub(self) -> Ed25519PublicKey:
        return self._ed25519_pub

    @override
    def get_ed25519(self) -> Ed25519PrivateKey:
        return self._ed25519

    def validate_files(self) -> bool:
        return (
            self._ed25519_pub.public_bytes_raw() == self.pub_file.read_bytes()
            and self._ed25519.private_bytes_raw() == self.priv_file.read_bytes()
        )


class Principal(BaseModel):
    hashkey: str = Field(description="hashkey of the principal")
    pubkey: str = Field(
        description="ed25519 public key is primary identifier of particular service instance"
    )

    @staticmethod
    def from_identity(identity: IdentityTrait) -> "Principal":
        return Principal(hashkey=identity.compute_hashkey(), pubkey=identity.pubkey)

    def get_identity(self) -> IdentityKey:
        return IdentityKey(self.pubkey)

    def get_ed25519_pub(self) -> Ed25519PublicKey:
        return Ed25519PublicKey.from_public_bytes(base64.b64decode(self.pubkey.encode("utf-8")))


class ServerConfig(BaseModel):
    service_principal: Principal = Field(description="The service principal of the server")
    http_endpoint: str = Field(description="The HTTP endpoint of the server")
    zmq_endpoint: str = Field(description="The ZMQ endpoint of the server")


class App(BaseModel):
    name: str = Field(description="name of the app")
    gref: GRef = Field(description="reference of the app module")
    owned_by: Principal = Field(description="The principal that owns the app")


class AppPremission(BaseModel):
    app: App = Field(description="The app of the permission")
    principal: Principal = Field(description="The principal of the permission")


class Grant(JsonBase):
    issued_at: datetime = Field(
        default_factory=datetime.now, description="The issued at time of the token"
    )
    expires_at: datetime | None = Field(default=None, description="The expires at time ")
    principal: Principal = Field(description="The principal of the token")

    def is_expired(self) -> bool:
        return self.expires_at is not None and self.expires_at < datetime.now()

    def seal_token(self, signer: SignerTrait) -> str:
        data = self.model_dump_json().encode("utf-8")
        sig = signer.sign(data)
        return f"{encode_base64(data)}.{encode_base64(sig)}"

    @classmethod
    def verify_token(cls, token: str, verifier: IdentityTrait) -> tuple[bool, "Grant | None"]:
        data, sig = token.split(".")
        data = base64.b64decode(data.encode("utf-8"))
        sig = base64.b64decode(sig.encode("utf-8"))
        if not verifier.verify(data, sig):
            return False, None
        return True, Grant.from_json(data.decode("utf-8"))
