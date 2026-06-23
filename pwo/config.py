from dataclasses import dataclass, field


@dataclass
class Config:
    concurrency: int = 10
    timeout: int = 15
    skip_axe: bool = True
    max_response_bytes: int = 500_000
    crawler_version: str = "0.1.0"
    user_agent: str = (
        "PublicWebObservatoryBot/0.1 "
        "(+https://publicwebobservatory.org/bot; contact: hello@publicwebobservatory.org)"
    )
    output_dir: str = "outputs"
    db_path: str = "outputs/observations.duckdb"
