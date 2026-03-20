#!/usr/bin/env python3
"""
Setup AWS Bedrock AgentCore Memory for XXXX Agent.

This script creates an AgentCore Memory with strategies and saves
the MEMORY_ID to the .env file.

Usage:
    python scripts/setup_memory.py
    python scripts/setup_memory.py --region us-west-2
    python scripts/setup_memory.py --name CustomMemoryName

Strategies configured:
    - SemanticStrategy: Automatic semantic fact extraction from conversations
    - UserPreferenceStrategy: Stores user preferences (teachers, times, courses)
    - EpisodicStrategy: Captures workflow experiences and interaction history

Note: AgentCore Memory allows one strategy per type, one namespace per strategy.
"""

import argparse
import os
import sys
from pathlib import Path


def create_memory_with_boto3(region: str, name: str) -> str:
    """
    Create an AgentCore Memory with all three strategies using boto3 directly.

    This approach is needed because the starter toolkit may not have
    EpisodicStrategy support yet.

    Args:
        region: AWS region
        name: Memory name

    Returns:
        The memory ID
    """
    import boto3

    client = boto3.client('bedrock-agentcore-control', region_name=region)

    print(f"Creating memory '{name}' in region '{region}'...")

    # Step 1: Create memory
    try:
        response = client.create_memory(
            name=name,
            description="XXXX Customer Service Agent Memory",
            eventExpiryDuration=365,  # days
        )
        memory_id = response['memory']['id']
        print(f"Memory created: {memory_id}")
    except client.exceptions.ConflictException:
        # Memory already exists, get it
        print(f"Memory '{name}' already exists, retrieving...")
        memories = client.list_memories()
        for m in memories.get('memories', []):
            if m.get('name') == name:
                memory_id = m['id']
                break
        else:
            raise Exception(f"Could not find memory '{name}'")
        print(f"Found existing memory: {memory_id}")

    # Step 2: Wait for memory to be ACTIVE
    print("Waiting for memory to become ACTIVE...")
    import time
    for _ in range(30):
        memory = client.get_memory(memoryId=memory_id)
        status = memory['memory']['status']
        if status == 'ACTIVE':
            print(f"Memory is ACTIVE")
            break
        print(f"  Status: {status}")
        time.sleep(2)
    else:
        print("Warning: Memory did not become ACTIVE in time")

    # Step 3: Add strategies
    existing_strategies = {s['type'] for s in memory['memory'].get('strategies', [])}

    strategies_to_add = []

    # Semantic strategy
    if 'SEMANTIC' not in existing_strategies:
        strategies_to_add.append({
            'semanticMemoryStrategy': {
                'name': f'{name}_semantic',
                'description': 'Semantic extraction for customer service conversations',
                'namespaces': ['/semantic/{actorId}/'],
            }
        })
        print(f"  Will add: SemanticStrategy")

    # User preference strategy
    if 'USER_PREFERENCE' not in existing_strategies:
        strategies_to_add.append({
            'userPreferenceMemoryStrategy': {
                'name': f'{name}_preferences',
                'description': 'User preferences: teachers, times, courses',
                'namespaces': ['/users/{actorId}/preferences/'],
            }
        })
        print(f"  Will add: UserPreferenceStrategy")

    # Episodic strategy
    if 'EPISODIC' not in existing_strategies:
        strategies_to_add.append({
            'episodicMemoryStrategy': {
                'name': f'{name}_episodic',
                'description': 'Episodic memory for booking history and workflow experiences',
                'namespaces': ['/strategies/{memoryStrategyId}/actors/{actorId}/sessions/{sessionId}/'],
                'reflectionConfiguration': {
                    'namespaces': ['/strategies/{memoryStrategyId}/actors/{actorId}/'],
                }
            }
        })
        print(f"  Will add: EpisodicStrategy")

    if strategies_to_add:
        print(f"\nAdding {len(strategies_to_add)} strategies...")
        client.update_memory(
            memoryId=memory_id,
            memoryStrategies={
                'addMemoryStrategies': strategies_to_add
            }
        )
        print("Strategies added successfully!")
    else:
        print("All strategies already configured.")

    # Verify final state
    memory = client.get_memory(memoryId=memory_id)
    print(f"\nFinal memory configuration:")
    print(f"  ID: {memory_id}")
    print(f"  Status: {memory['memory']['status']}")
    print(f"  Strategies:")
    for s in memory['memory'].get('strategies', []):
        print(f"    - {s['name']} ({s['type']})")

    return memory_id


def create_memory_with_toolkit(region: str, name: str) -> str:
    """
    Create an AgentCore Memory using the starter toolkit.

    Note: This may not include EpisodicStrategy if toolkit version is old.

    Args:
        region: AWS region
        name: Memory name

    Returns:
        The memory ID
    """
    try:
        from bedrock_agentcore_starter_toolkit.operations.memory.manager import MemoryManager
        from bedrock_agentcore_starter_toolkit.operations.memory.models.strategies import (
            SemanticStrategy,
            UserPreferenceStrategy,
        )
    except ImportError as e:
        print(f"Toolkit not available: {e}")
        print("Falling back to boto3...")
        return create_memory_with_boto3(region, name)

    # Check if EpisodicStrategy is available
    try:
        from bedrock_agentcore_starter_toolkit.operations.memory.models.strategies import EpisodicStrategy
        has_episodic = True
    except ImportError:
        has_episodic = False
        print("Note: EpisodicStrategy not available in toolkit, will add via boto3")

    print(f"Creating memory '{name}' in region '{region}'...")

    manager = MemoryManager(region_name=region)

    # Define strategies (without episodic if not available)
    strategies = [
        SemanticStrategy(
            name=f"{name}_semantic",
            description="Semantic extraction for customer service conversations",
            namespaces=["/semantic/{actorId}/"],
        ),
        UserPreferenceStrategy(
            name=f"{name}_preferences",
            description="User preferences: teachers, times, courses",
            namespaces=["/users/{actorId}/preferences/"],
        ),
    ]

    if has_episodic:
        strategies.append(EpisodicStrategy(
            name=f"{name}_episodic",
            description="Episodic memory for booking history and workflow experiences",
            namespaces=["/strategies/{memoryStrategyId}/actors/{actorId}/sessions/{sessionId}/"],
        ))

    print("Strategies to be configured:")
    for strategy in strategies:
        print(f"  - {strategy.__class__.__name__}: {strategy.name}")

    # Create memory with strategies
    print("\nCreating memory...")
    memory = manager.get_or_create_memory(
        name=name,
        strategies=strategies,
    )

    memory_id = memory.get('id') or memory.get('memoryId')
    print(f"Memory ID: {memory_id}")

    # If episodic was not available in toolkit, add it via boto3
    if not has_episodic:
        print("\nAdding EpisodicStrategy via boto3...")
        import boto3
        client = boto3.client('bedrock-agentcore-control', region_name=region)
        client.update_memory(
            memoryId=memory_id,
            memoryStrategies={
                'addMemoryStrategies': [
                    {
                        'episodicMemoryStrategy': {
                            'name': f'{name}_episodic',
                            'description': 'Episodic memory for booking history and workflow experiences',
                            'namespaces': ['/strategies/{memoryStrategyId}/actors/{actorId}/sessions/{sessionId}/'],
                            'reflectionConfiguration': {
                                'namespaces': ['/strategies/{memoryStrategyId}/actors/{actorId}/'],
                            }
                        }
                    }
                ]
            }
        )
        print("EpisodicStrategy added!")

    return memory_id


def update_env_file(memory_id: str, env_path: Path) -> None:
    """
    Update the .env file with the MEMORY_ID.

    Args:
        memory_id: The memory ID to save
        env_path: Path to the .env file
    """
    env_content = ""

    if env_path.exists():
        env_content = env_path.read_text()

    # Check if MEMORY_ID already exists
    lines = env_content.splitlines()
    updated = False

    for i, line in enumerate(lines):
        if line.startswith("MEMORY_ID="):
            lines[i] = f"MEMORY_ID={memory_id}"
            updated = True
            break

    if not updated:
        # Add MEMORY_ID at the end
        if env_content and not env_content.endswith("\n"):
            lines.append("")
        lines.append(f"# AgentCore Memory (created by setup_memory.py)")
        lines.append(f"MEMORY_ID={memory_id}")

    env_path.write_text("\n".join(lines) + "\n")
    print(f"Updated {env_path} with MEMORY_ID")


def main():
    parser = argparse.ArgumentParser(
        description="Setup AWS Bedrock AgentCore Memory for XXXX Agent"
    )
    parser.add_argument(
        "--region",
        default=os.environ.get("AWS_REGION", "us-west-2"),
        help="AWS region (default: AWS_REGION env var or us-west-2)",
    )
    parser.add_argument(
        "--name",
        default="XXXXMemory",
        help="Memory name (default: XXXXMemory)",
    )
    parser.add_argument(
        "--env-file",
        default=".env",
        help="Path to .env file (default: .env)",
    )
    parser.add_argument(
        "--use-boto3",
        action="store_true",
        help="Use boto3 directly instead of toolkit",
    )

    args = parser.parse_args()

    # Resolve env file path relative to project root
    project_root = Path(__file__).parent.parent
    env_path = project_root / args.env_file

    print("=" * 60)
    print("XXXX AgentCore Memory Setup")
    print("=" * 60)
    print(f"Region: {args.region}")
    print(f"Memory Name: {args.name}")
    print(f"Env File: {env_path}")
    print("=" * 60)
    print()
    print("Memory Strategies (3 types):")
    print("  1. SemanticStrategy - Automatic fact extraction")
    print("     Namespace: /semantic/{actorId}/")
    print("  2. UserPreferenceStrategy - User preferences")
    print("     Namespace: /users/{actorId}/preferences/")
    print("  3. EpisodicStrategy - Workflow experiences")
    print("     Namespace: /strategies/{memoryStrategyId}/actors/{actorId}/sessions/{sessionId}/")
    print("     Reflection: /strategies/{memoryStrategyId}/actors/{actorId}/")
    print()
    print("=" * 60)

    # Create the memory
    if args.use_boto3:
        memory_id = create_memory_with_boto3(args.region, args.name)
    else:
        memory_id = create_memory_with_toolkit(args.region, args.name)

    # Update .env file
    update_env_file(memory_id, env_path)

    print()
    print("=" * 60)
    print("Setup Complete!")
    print("=" * 60)
    print(f"Memory ID: {memory_id}")
    print()
    print("Configured strategies:")
    print(f"  - {args.name}_semantic (SEMANTIC)")
    print(f"  - {args.name}_preferences (USER_PREFERENCE)")
    print(f"  - {args.name}_episodic (EPISODIC)")
    print()
    print("You can now run the agent with memory enabled:")
    print("  python run_agent.py")
    print("  python run_agent.py --memory-mode tool   # Agent-driven search (default)")
    print("  python run_agent.py --memory-mode hook   # Auto search")
    print()
    print("To disable memory, use:")
    print("  python run_agent.py --memory-mode disabled")
    print()
    print("For AgentCore Runtime deployment, update Dockerfile:")
    print(f"  ENV MEMORY_ID={memory_id}")


if __name__ == "__main__":
    main()
