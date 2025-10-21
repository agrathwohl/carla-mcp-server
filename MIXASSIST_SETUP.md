# MixAssist Dataset Setup Guide

This guide explains how to download and configure the MixAssist dataset for use with the Carla MCP Server.

## What is MixAssist?

MixAssist is a professional audio engineering dataset containing 640 conversations covering:
- Drum mixing techniques
- Guitar processing
- Bass production
- Vocal engineering
- Keyboard/synth mixing
- Overall mix strategies

The dataset provides contextual mixing advice and real-world troubleshooting from professional audio engineers.

**Research Paper**: [MixAssist: Instruction-Tuned LLMs as AI Mixing Assistants](https://arxiv.org/html/2507.06329v1)

## Quick Setup (Recommended)

### 1. Download and Configure

Run the automated setup script:

```bash
# Download to default location (~/.cache/mixassist/data) and create config
python setup_mixassist.py --download

# Or specify custom location
python setup_mixassist.py --download --output ~/datasets/mixassist
```

This will:
- Download the dataset from Hugging Face (requires `datasets` package)
- Verify the download integrity
- Create a `.env` configuration file
- Display confirmation and next steps

### 2. Restart MCP Server

If the MCP server is already running, restart it to load the new configuration:

```bash
# Stop the current server (Ctrl+C)
# Then restart
python server.py
```

### 3. Verify Access

The MixAssist resources will now be available via MCP URIs:
- `mixassist://index` - Topic overview
- `mixassist://advice/drums/top5` - Top drum mixing tips
- `mixassist://search?q=compression` - Search conversations

## Manual Setup

### Prerequisites

Install required Python packages:

```bash
pip install datasets pandas pyarrow
```

### Option 1: Download with Script

```bash
# Download only (skip config creation)
python setup_mixassist.py --download --no-config

# Later, create config for existing dataset
python setup_mixassist.py --path /path/to/dataset
```

### Option 2: Manual Download

1. **Download from Hugging Face**:
   ```python
   from datasets import load_dataset

   dataset = load_dataset("MixAssist/mixassist", trust_remote_code=True)

   for split_name, split_data in dataset.items():
       split_data.to_parquet(f"{split_name}-00000-of-00001.parquet")
   ```

2. **Create Configuration File**:

   Create a `.env` file in the project root:
   ```bash
   # .env
   MIXASSIST_DATASET_PATH=/path/to/your/dataset
   MIXASSIST_ENABLED=true
   ```

3. **Verify Dataset**:
   ```bash
   python setup_mixassist.py --verify --path /path/to/dataset
   ```

## Configuration Options

### Environment Variables

Configure MixAssist behavior via environment variables or `.env` file:

```bash
# Required: Path to dataset directory
MIXASSIST_DATASET_PATH=/home/user/.cache/mixassist/data

# Optional: Enable/disable MixAssist resources (default: true)
MIXASSIST_ENABLED=true
```

### Configuration File Locations

The system looks for configuration in this order:

1. `.env` file in project root
2. Environment variables (override file config)

### Disabling MixAssist

To temporarily disable MixAssist resources without removing the dataset:

```bash
# In .env file
MIXASSIST_ENABLED=false

# Or as environment variable
export MIXASSIST_ENABLED=false
```

## Dataset Structure

The downloaded dataset contains three splits:

```
dataset/
├── train-00000-of-00001.parquet      # 340 conversations
├── test-00000-of-00001.parquet       # 250 conversations
└── validation-00000-of-00001.parquet # 50 conversations
```

**Total**: 640 professional audio engineering conversations

**Topic Distribution**:
- Drums: 138 conversations
- Overall Mix: 93 conversations
- Guitars: 58 conversations
- Bass: 18 conversations
- Vocals: 18 conversations
- Keys: 15 conversations

## Using MixAssist Resources

### Resource URIs

Once configured, access MixAssist data via these URIs:

#### Index Resources (Tiny - <1K tokens)
```
mixassist://index                    # Topic counts and sample IDs
mixassist://schema                   # Dataset schema information
```

#### Topic Indexes (Small - <500 tokens)
```
mixassist://index/drums              # All drum conversation IDs
mixassist://index/guitars            # All guitar conversation IDs
mixassist://index/bass               # All bass conversation IDs
mixassist://index/vocals             # All vocal conversation IDs
mixassist://index/keys               # All keys conversation IDs
mixassist://index/overall_mix        # All overall mix IDs
```

#### Curated Advice (Small - <3K tokens)
```
mixassist://advice/drums/top5        # Top 5 drum mixing tips
mixassist://advice/guitars/top5      # Top 5 guitar tips
mixassist://advice/bass/top5         # Top 5 bass tips
mixassist://advice/vocals/top5       # Top 5 vocal tips
mixassist://advice/keys/top5         # Top 5 keys tips
mixassist://advice/overall_mix/top5  # Top 5 overall mix tips
```

#### Search (Medium - <5K tokens)
```
mixassist://search?q=compression     # Search for "compression"
mixassist://search?q=multiband       # Search for "multiband"
mixassist://search?q=sidechain       # Search for "sidechain"
```

#### Individual Conversations (Medium - <1K tokens each)
```
mixassist://conversation/{conv_id}   # Get specific conversation
```

### Token-Efficient Access Pattern

**Best Practice**: Always use the hierarchical pattern to minimize token usage:

1. **Start with index** → See topic counts
2. **Browse top5 advice** → Get curated best practices
3. **Search if needed** → Find specific techniques
4. **Fetch conversations** → Only when top5/search insufficient

### Example: Using in Claude Code

```markdown
User: "Help me with drum overhead compression"

AI (internally): Let me check MixAssist for professional advice
   ReadMcpResourceTool(server="carla-mcp-server", uri="mixassist://advice/drums/top5")

AI: Based on professional mixing techniques, here's how to approach drum overhead compression:

[Curated advice from MixAssist top 5 drum tips]

In my experience, multiband compression on overheads works particularly well for
controlling cymbal harshness while maintaining the natural drum ambience. Try setting
a ratio of 3:1 on the high band (above 8kHz) with a slower attack (30ms) to preserve
transients.

Would you like me to set up these parameters on your overhead bus?
```

## Troubleshooting

### Dataset Not Loading

**Symptom**: Resources show as unavailable or errors when accessing

**Solutions**:
1. Verify dataset path is correct:
   ```bash
   python setup_mixassist.py --verify --path /your/dataset/path
   ```

2. Check .env configuration:
   ```bash
   cat .env | grep MIXASSIST
   ```

3. Ensure all required files exist:
   ```bash
   ls -lh /path/to/dataset/*.parquet
   # Should show: train, test, validation parquet files
   ```

### Permission Errors

**Symptom**: Cannot write to cache directory

**Solution**: Use a writable location:
```bash
python setup_mixassist.py --download --output ~/mixassist_data
```

### Hugging Face Authentication

**Symptom**: Download fails with authentication error

**Solution**: Login to Hugging Face:
```bash
pip install huggingface-hub
huggingface-cli login
# Then retry download
python setup_mixassist.py --download --force
```

### Memory Issues

**Symptom**: Server uses too much memory

**Solution**: MixAssist loads lazily - data is only loaded when first accessed. If memory is still an issue:
1. Disable MixAssist temporarily:
   ```bash
   # In .env
   MIXASSIST_ENABLED=false
   ```

2. Or completely uninstall:
   ```bash
   rm -rf ~/.cache/mixassist
   # Remove from .env:
   # MIXASSIST_DATASET_PATH=...
   ```

## Advanced Usage

### Custom Dataset Location

If you need to store the dataset in a specific location (e.g., on a different drive):

```bash
# Download to custom location
python setup_mixassist.py --download --output /mnt/data/mixassist

# Or manually configure
echo "MIXASSIST_DATASET_PATH=/mnt/data/mixassist" >> .env
```

### Programmatic Access

You can also access MixAssist resources programmatically:

```python
from mixassist_resources import MixAssistResourceProvider

# Initialize with custom path
provider = MixAssistResourceProvider(dataset_path="/path/to/dataset")

# Check availability
if provider.is_available():
    # Get curated advice
    advice = provider.get_resource_content("mixassist://advice/drums/top5")
    print(advice)

    # Search conversations
    results = provider.get_resource_content("mixassist://search?q=compression")
    print(results)
```

## Dataset Information

### Dataset Statistics

- **Total Conversations**: 640
- **Splits**: Train (340), Test (250), Validation (50)
- **Topics**: 6 (Drums, Overall Mix, Guitars, Bass, Vocals, Keys)
- **Average Conversation Length**: ~200-500 tokens
- **Format**: Apache Parquet (efficient columnar storage)

### Data Schema

Each conversation contains:
- `conversation_id`: Unique identifier
- `topic`: Audio mixing domain
- `turn_id`: Sequential turn number
- `input_history`: Previous conversation context
- `user`: Engineer's question
- `assistant`: Expert mixing advice
- `audio_file`: Referenced audio (metadata only)

### Research Citation

If you use MixAssist in research or production, please cite:

```bibtex
@article{mixassist2024,
  title={MixAssist: Instruction-Tuned LLMs as AI Mixing Assistants},
  author={[Authors]},
  journal={arXiv preprint arXiv:2507.06329},
  year={2024},
  url={https://arxiv.org/html/2507.06329v1}
}
```

## Support

For issues with MixAssist setup:

1. Check [MIXASSIST_SETUP.md](MIXASSIST_SETUP.md) (this file)
2. Review logs: `carla_mcp_server.log`
3. File an issue: [GitHub Issues](https://github.com/your-org/carla-mcp-server/issues)
4. Include:
   - Python version
   - Output of `python setup_mixassist.py --verify --path /your/path`
   - Relevant log messages

---

**Ready to enhance your mixing workflow with professional audio engineering knowledge!** 🎛️✨