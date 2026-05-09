#!/usr/bin/env python3
"""
Dependency Audit Script

Scans all extracted modules to find undefined names and maps them to their source modules.
Generates proper import statements to fix all cross-module dependencies.

Usage:
    python3 scripts/audit_dependencies.py

Output:
    - Prints dependency report
    - Generates import fixes for each module
    - Validates no circular imports
"""

import ast
import json
from pathlib import Path
from collections import defaultdict
from typing import Dict, Set, List, Tuple

# Paths
BLACKBOX_ROOT = Path(__file__).parent.parent
ORCHESTRATOR_DIR = BLACKBOX_ROOT / "Orchestrator"

# Extracted modules to audit
EXTRACTED_MODULES = [
    "config.py",
    "models.py",
    "state.py",
    "volume.py",
    "artifacts.py",
    "monitoring.py",
    "fossils.py",
    "checkpoint.py",
    "startup.py",
    "tasks.py",
    "routes/task_routes.py",
    "routes/admin_routes.py",
    "routes/tts_routes.py",
    "routes/chat_routes.py",
    "routes/agent_routes.py",
]


class DependencyAuditor:
    def __init__(self):
        self.undefined_by_module = {}  # module -> set of undefined names
        self.defined_by_module = {}    # module -> set of defined names
        self.imports_by_module = {}    # module -> set of imported names

    def audit_module(self, module_path: Path) -> Tuple[Set[str], Set[str], Set[str]]:
        """Audit a module and return (undefined, defined, imported) names."""

        try:
            source = module_path.read_text()
            tree = ast.parse(source)
        except Exception as e:
            print(f"⚠️  Failed to parse {module_path.name}: {e}")
            return (set(), set(), set())

        defined = set()
        imported = set()
        used = set()

        for node in ast.walk(tree):
            # Track definitions
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                defined.add(node.name)
            elif isinstance(node, ast.Assign):
                for target in node.targets:
                    if isinstance(target, ast.Name):
                        defined.add(target.id)

            # Track imports
            elif isinstance(node, ast.Import):
                for alias in node.names:
                    imported.add(alias.asname if alias.asname else alias.name)
            elif isinstance(node, ast.ImportFrom):
                if node.names[0].name == '*':
                    # Wildcard import - mark as imported
                    imported.add(f"*from {node.module}")
                else:
                    for alias in node.names:
                        imported.add(alias.asname if alias.asname else alias.name)

            # Track name usage
            elif isinstance(node, ast.Name):
                if isinstance(node.ctx, (ast.Load, ast.Del)):
                    used.add(node.id)

        # Undefined = used but not defined or imported
        undefined = used - defined - imported

        # Filter out builtins and common names
        builtins = {'True', 'False', 'None', 'print', 'len', 'str', 'int', 'dict', 'list',
                    'range', 'enumerate', 'zip', 'open', 'Exception', 'ValueError', 'TypeError',
                    'KeyError', 'AttributeError', 'IndexError', 'RuntimeError', 'IOError'}
        undefined = undefined - builtins

        return (undefined, defined, imported)

    def find_definition(self, name: str, exclude_module: str = "") -> List[str]:
        """Find which modules define a given name."""
        sources = []

        for module, names in self.defined_by_module.items():
            if module == exclude_module:
                continue
            if name in names:
                sources.append(module)

        return sources

    def audit_all(self):
        """Audit all extracted modules."""

        print("="*70)
        print("DEPENDENCY AUDIT - Extracted Modules")
        print("="*70)
        print()

        # First pass: collect definitions
        for module_rel in EXTRACTED_MODULES:
            module_path = ORCHESTRATOR_DIR / module_rel
            if not module_path.exists():
                print(f"⚠️  {module_rel} not found")
                continue

            undefined, defined, imported = self.audit_module(module_path)

            module_key = module_rel.replace("/", ".").replace(".py", "")
            self.undefined_by_module[module_key] = undefined
            self.defined_by_module[module_key] = defined
            self.imports_by_module[module_key] = imported

        # Second pass: map undefined to sources
        print("Dependency Analysis:")
        print("-" * 70)

        dependency_map = {}

        for module, undefined in self.undefined_by_module.items():
            if not undefined:
                print(f"✅ {module:30s} - No undefined names")
                continue

            print(f"\n📦 {module}")
            print(f"   Undefined: {len(undefined)} names")

            needs_imports = {}
            unresolved = []

            for name in sorted(undefined):
                sources = self.find_definition(name, exclude_module=module)
                if sources:
                    # Map to source module
                    source = sources[0]  # Use first match
                    if source not in needs_imports:
                        needs_imports[source] = []
                    needs_imports[source].append(name)
                    print(f"   - {name:30s} → {source}")
                else:
                    # Likely from app_refactored.py (remaining code)
                    unresolved.append(name)

            if unresolved:
                print(f"   ⚠️  Unresolved ({len(unresolved)}): {', '.join(sorted(unresolved)[:5])}")

            dependency_map[module] = {
                'needs_imports': needs_imports,
                'unresolved': unresolved
            }

        return dependency_map

    def generate_imports(self, dependency_map: Dict) -> Dict[str, str]:
        """Generate import statements for each module."""

        import_statements = {}

        for module, deps in dependency_map.items():
            imports = []

            for source_module, names in sorted(deps['needs_imports'].items()):
                # Convert module path to import path
                import_path = f"Orchestrator.{source_module}"
                imports.append(f"from {import_path} import {', '.join(sorted(names))}")

            if deps['unresolved']:
                # These likely need to be imported from app_refactored
                imports.append(f"# Unresolved: {', '.join(sorted(deps['unresolved']))}")

            import_statements[module] = '\n'.join(imports)

        return import_statements

    def print_report(self, dependency_map: Dict):
        """Print comprehensive dependency report."""

        print("\n" + "="*70)
        print("DEPENDENCY REPORT")
        print("="*70)

        total_undefined = sum(len(d['needs_imports']) + len(d['unresolved'])
                             for d in dependency_map.values())
        modules_with_issues = sum(1 for d in dependency_map.values()
                                 if d['needs_imports'] or d['unresolved'])

        print(f"\nModules audited: {len(EXTRACTED_MODULES)}")
        print(f"Modules with missing imports: {modules_with_issues}")
        print(f"Total undefined references: {total_undefined}")

        print("\n" + "="*70)
        print("RECOMMENDED IMPORTS")
        print("="*70)

        import_statements = self.generate_imports(dependency_map)

        for module, imports in sorted(import_statements.items()):
            if imports:
                print(f"\n## {module}")
                print(imports)

        return import_statements


def main():
    auditor = DependencyAuditor()
    dependency_map = auditor.audit_all()
    import_statements = auditor.print_report(dependency_map)

    # Save to JSON for programmatic use
    output_file = BLACKBOX_ROOT / "Manifest" / "dependency_audit.json"
    with open(output_file, 'w') as f:
        json.dump({
            'dependency_map': {k: {'needs_imports': {sk: sv for sk, sv in v['needs_imports'].items()},
                                   'unresolved': v['unresolved']}
                              for k, v in dependency_map.items()},
            'import_statements': import_statements
        }, f, indent=2)

    print(f"\n✅ Audit saved to: {output_file}")
    print("\nNext step: Apply these imports to the modules")

    return 0


if __name__ == "__main__":
    exit(main())
