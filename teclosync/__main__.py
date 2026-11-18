import argparse
import json
from .config import load_sources

def main():
    parser = argparse.ArgumentParser(description='Teclosync source registry tools; no network or publication')
    parser.add_argument('command', choices=['validate-config', 'list-sources'])
    parser.add_argument('--config', default='config/sources.json')
    args = parser.parse_args()
    try:
        sources = load_sources(args.config)
    except (ValueError, OSError) as error:
        parser.exit(1, f'Configuration error: {error}\n')
    if args.command == 'list-sources':
        print(json.dumps(sources, indent=2, ensure_ascii=False))
    else:
        print(json.dumps({'valid': True, 'sources': len(sources), 'enabled': sum(s['enabled'] for s in sources)}))

if __name__ == '__main__':
    main()
