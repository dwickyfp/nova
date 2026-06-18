# Architecture 02: Storage Provider Layer

> Storage-agnostic abstraction: one interface, multiple backends.
> Credentials live in `nova.yaml`, never in database.

---

## Architecture

```
nova.yaml                         Stage (in NOVA_SYSTEM.CONFIG)
├── storage:                      ├── name: "stage1"
│   connections:                  ├── storage_connection: "production" ← ref
│     production:                 └── base_prefix: "datalake/bronze/stage1"
│       type: minio                      │
│       endpoint: minio:9000             │ lookup
│       access_key: ${MINIO_AK}          ▼
│       secret_key: ${MINIO_SK}   config.storage_connections["production"]
└── .env                             → StorageConfig(type="minio", ...)
   └── MINIO_AK, MINIO_SK              → S3Provider(config)
                                         → provider.get_files_params(key, fmt)
```

---

## StorageProvider Interface

```python
from abc import ABC, abstractmethod
from dataclasses import dataclass

@dataclass
class FileEntry:
    name: str              # "data_pembayaran.csv"
    path: str              # relative path from stage root
    size: int              # bytes
    modified: str          # ISO timestamp
    is_folder: bool = False

@dataclass
class StorageConnectionConfig:
    """Loaded from nova.yaml — NOT from database."""
    name: str              # "production" (key in nova.yaml)
    type: str              # minio, s3, azure_blob, gcs
    endpoint: str
    bucket: str
    access_key: str
    secret_key: str
    region: str = ""
    path_style: bool = True
    ssl: bool = False


class StorageProvider(ABC):
    """Unified interface for all storage backends."""
    
    def __init__(self, config: StorageConnectionConfig):
        self.config = config
    
    @abstractmethod
    def list(self, prefix: str) -> list[FileEntry]:
        pass
    
    @abstractmethod
    def upload(self, key: str, data: bytes) -> None:
        pass
    
    @abstractmethod
    def download(self, key: str) -> bytes:
        pass
    
    @abstractmethod
    def delete(self, key: str) -> None:
        pass
    
    @abstractmethod
    def exists(self, key: str) -> bool:
        pass
    
    @abstractmethod
    def get_files_params(self, key: str, fmt: str) -> str:
        """
        Generate FILES() SQL parameters.
        Each provider generates provider-specific syntax.
        """
        pass
```

---

## Provider Implementations

### S3Provider (S3 / MinIO / OSS / Ceph)

```python
class S3Provider(StorageProvider):
    def __init__(self, config: StorageConnectionConfig):
        super().__init__(config)
        self.client = boto3.client(
            "s3",
            endpoint_url=config.endpoint or None,
            aws_access_key_id=config.access_key,
            aws_secret_access_key=config.secret_key,
            region_name=config.region or "us-east-1",
        )
    
    def list(self, prefix: str) -> list[FileEntry]:
        resp = self.client.list_objects_v2(
            Bucket=self.config.bucket, Prefix=prefix, Delimiter="/"
        )
        entries = []
        for folder in resp.get("CommonPrefixes", []):
            name = folder["Prefix"].rstrip("/").split("/")[-1]
            entries.append(FileEntry(name=name, path=folder["Prefix"],
                                      size=0, modified="", is_folder=True))
        for obj in resp.get("Contents", []):
            if obj["Key"].endswith("/"):
                continue
            entries.append(FileEntry(
                name=obj["Key"].split("/")[-1],
                path=obj["Key"],
                size=obj["Size"],
                modified=obj["LastModified"].isoformat()
            ))
        return entries
    
    def upload(self, key: str, data: bytes):
        self.client.put_object(Bucket=self.config.bucket, Key=key, Body=data)
    
    def download(self, key: str) -> bytes:
        resp = self.client.get_object(Bucket=self.config.bucket, Key=key)
        return resp["Body"].read()
    
    def delete(self, key: str):
        self.client.delete_object(Bucket=self.config.bucket, Key=key)
    
    def exists(self, key: str) -> bool:
        try:
            self.client.head_object(Bucket=self.config.bucket, Key=key)
            return True
        except:
            return False
    
    def get_files_params(self, key: str, fmt: str) -> str:
        path = f"s3://{self.config.bucket}/{key}"
        return f"""
            'path' = '{path}',
            'format' = '{fmt}',
            'aws.s3.endpoint' = '{self.config.endpoint}',
            'aws.s3.access_key' = '{self.config.access_key}',
            'aws.s3.secret_key' = '{self.config.secret_key}',
            'aws.s3.enable_path_style_access' = '{str(self.config.path_style).lower()}',
            'aws.s3.enable_ssl' = '{str(self.config.ssl).lower()}'
        """
```

### AzureBlobProvider

```python
class AzureBlobProvider(StorageProvider):
    def __init__(self, config: StorageConnectionConfig):
        super().__init__(config)
        from azure.storage.blob import BlobServiceClient
        self.client = BlobServiceClient(
            account_url=f"https://{config.endpoint}.blob.core.windows.net",
            credential=config.access_key
        )
    
    def list(self, prefix: str) -> list[FileEntry]:
        container = self.client.get_container_client(self.config.bucket)
        entries = []
        for blob in container.list_blobs(name_starts_with=prefix):
            name = blob.name.split("/")[-1]
            if not name:
                continue
            entries.append(FileEntry(
                name=name, path=blob.name,
                size=blob.size,
                modified=blob.last_modified.isoformat()
            ))
        return entries
    
    def get_files_params(self, key: str, fmt: str) -> str:
        path = f"wasbs://{self.config.bucket}@{self.config.endpoint}.blob.core.windows.net/{key}"
        return f"""
            'path' = '{path}',
            'format' = '{fmt}',
            'azure.blob.storage_account' = '{self.config.endpoint}',
            'azure.blob.container' = '{self.config.bucket}',
            'azure.blob.shared_key' = '{self.config.access_key}'
        """
```

### GCSProvider

```python
class GCSProvider(StorageProvider):
    def __init__(self, config: StorageConnectionConfig):
        super().__init__(config)
        from google.cloud import storage
        self.client = storage.Client.from_service_account_json(config.access_key)
    
    def list(self, prefix: str) -> list[FileEntry]:
        bucket = self.client.bucket(self.config.bucket)
        entries = []
        for blob in bucket.list_blobs(prefix=prefix):
            name = blob.name.split("/")[-1]
            if not name:
                continue
            entries.append(FileEntry(
                name=name, path=blob.name,
                size=blob.size,
                modified=blob.updated.isoformat()
            ))
        return entries
    
    def get_files_params(self, key: str, fmt: str) -> str:
        import json
        path = f"gs://{self.config.bucket}/{key}"
        creds = json.loads(self.config.access_key)
        return f"""
            'path' = '{path}',
            'format' = '{fmt}',
            'gcp.gcs.service_account_email' = '{creds["client_email"]}',
            'gcp.gcs.service_account_private_key' = '{creds["private_key"]}'
        """
```

---

## Factory + Config Loading

```python
# storage/factory.py
class StorageFactory:
    _providers = ***
        "minio": S3Provider,
        "s3": S3Provider,
        "azure_blob": AzureBlobProvider,
        "gcs": GCSProvider,
        "oss": S3Provider,
        "ceph": S3Provider,
    }
    
    @classmethod
    def create(cls, config: StorageConnectionConfig) -> StorageProvider:
        provider_cls = cls._providers.get(config.type)
        if not provider_cls:
            raise ValueError(f"Unsupported storage type: {config.type}")
        return provider_cls(config)


# core/config.py
def load_storage_connections(path: str = "nova.yaml") -> dict[str, StorageConnectionConfig]:
    """Load storage connections from nova.yaml."""
    with open(path) as f:
        raw = yaml.safe_load(f)
    raw = _substitute_env(raw)
    
    connections = {}
    for name, cfg in raw.get("storage", {}).get("connections", {}).items():
        connections[name] = StorageConnectionConfig(name=name, **cfg)
    
    return connections


# Global
storage_connections = load_storage_connections()


def resolve_stage_storage(stage_name: str, db: str, schema: str) -> StorageProvider:
    """Resolve stage → storage provider via nova.yaml."""
    # Get stage from NOVA_SYSTEM.CONFIG
    stage = stage_repo.find(db, schema, stage_name)
    if not stage:
        raise ValueError(f"Stage '{stage_name}' not found in {db}.{schema}")
    
    # Get connection config from nova.yaml
    conn_config = storage_connections.get(stage.storage_connection)
    if not conn_config:
        raise ValueError(f"Storage connection '{stage.storage_connection}' not in nova.yaml")
    
    return StorageFactory.create(conn_config)
```

---

## Adding New Providers

```python
# 1. Create provider class
class MinIOIAMProvider(S3Provider):
    """MinIO with STS/IAM auth."""
    def get_files_params(self, key, fmt):
        # Different params for IAM
        ...

# 2. Register
StorageFactory.register("minio_iam", MinIOIAMProvider)

# 3. Add to nova.yaml
# storage.connections.minio_iam_prod:
#   type: minio_iam
#   ...

# No changes in NOVA_SYSTEM, SQL Dialect, or UI.
```
