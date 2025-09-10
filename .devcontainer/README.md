# Music Assi5. Press `F5` to start Music Assistant locally
6. Open your browser and navigate to `http://localhost:8095` to access the UIant Development Container

This directory contains the configuration for the Music Assistant development container, which provides a consistent development environment across different platforms.

## Quick Start

1. Install [Docker Desktop](https://www.docker.com/products/docker-desktop/) and [VS Code](https://code.visualstudio.com/)
2. Install the [Dev Containers](https://marketplace.visualstudio.com/items?itemName=ms-vscode-remote.remote-containers) extension for VS Code
3. Clone this repository
4. Open the repository in VS Code
5. Press `F1` and select "Dev Containers: Reopen in Container"
6. Wait for the container to build and dependencies to install
7. Press `F5` to start Music Assistant locally
8. Open the forwarded port for 8095 to access the UI

## Platform-Specific Requirements

### Windows Users

**Before opening the devcontainer**, configure Git to use LF line endings:

```bash
git config --global core.eol lf
git config --global core.autocrlf input
```

If you already have the repository cloned with CRLF line endings, fix it with:

```bash
git rm -rf --cached .
git reset --hard HEAD
```

⚠️ **WARNING:** The above commands will reset any uncommitted changes.

**Docker Desktop Configuration:**
- Enable host networking in Docker Desktop settings
- This is required for Music Assistant's networking features to work properly

### Linux/macOS Users

No special configuration is required. The devcontainer will work out of the box.

## Container Features

- **Host Networking**: Enabled for proper Music Assistant networking functionality
- **Persistent Storage**: Music Assistant configuration is persisted across container rebuilds
- **Pre-configured Environment**: Python virtual environment with all dependencies installed
- **VS Code Integration**: Debugging, formatting, and linting pre-configured

## Development Workflow

1. **Start Development**: Press `F5` in VS Code to launch Music Assistant with debugging
2. **Access UI**: Open your browser and navigate to `http://localhost:8095`
3. **View Logs**: Debug output will appear in the VS Code Debug Console
4. **Make Changes**: Edit code and restart with `F5` to see changes
5. **Run Tests**: Use the integrated terminal to run tests with `pytest`

## Troubleshooting

### Port Forwarding Issues
- **No port forwarding is used** - the container uses host networking instead
- Access Music Assistant directly at `http://localhost:8095`
- Port conflicts are resolved automatically during startup
- If you see "address already in use" errors, rebuild the container

### Volume Mount Issues
- The container uses a named volume (`musicassistant-data`) for cross-platform compatibility
- Permissions are automatically fixed during container startup
- Port conflicts are automatically resolved by killing existing processes
- If you need to reset your configuration, remove the volume: `docker volume rm musicassistant-data`

### Network Issues
- Ensure Docker Desktop has host networking enabled (Windows)
- On Linux, the container runs with `--network=host` for proper functionality

### Permission Issues
- The container runs as user `mass` with appropriate permissions
- If you encounter permission issues, try rebuilding the container: "Dev Containers: Rebuild Container"

## Local Music Library (Optional)

To access a local music library from within the devcontainer:

1. Edit `.devcontainer/devcontainer.json`
2. Uncomment and modify the music library mount:
   ```json
   "source=/path/to/your/music,target=/music,type=bind,consistency=cached,readonly"
   ```
3. Rebuild the container

## Additional Resources

- [Music Assistant Documentation](https://music-assistant.io)
- [Development Guidelines](../DEVELOPMENT.md)
- [VS Code Dev Containers Documentation](https://code.visualstudio.com/docs/remote/containers)
