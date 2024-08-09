import pkgutil
from dataclasses import dataclass

from hydra.core.config_store import ConfigStore

from configs.infrastructure.infrastructure_schemas import InfrastructureConfig


@dataclass
class Config:
    infrastructure: InfrastructureConfig = InfrastructureConfig()
    
    
    
def register_config() -> None:
    cs = ConfigStore.instance()
    cs.store(name="config", node=Config)
    
    for module_info in pkgutil.walk_packages(__path__):
        name = module_info.name
        module_finder = module_info.module_finder
        
        module = module_finder.find_module(name).load_module(name)
        
        if hasattr(module, '_register_configs'):
            module._register_configs()