# Git SSH Authentication in Non-Interactive Processes

**Extracted:** 2026-04-24
**Context:** Node.js CLI tools that spawn git processes for SSH repositories - authentication prompts when it shouldn't

## Problem

When spawning git commands that access SSH repositories from Node.js:
- User has working SSH authentication in terminal
- But when spawned from Node.js, git asks for username/password
- This happens even though `ssh-agent` is running and key is loaded
- The process hangs waiting for interactive input

**Root causes** that were ruled out and fixed:
1. `BatchMode=yes` not set → git hangs on auth failure
2. Overriding `GIT_SSH_COMMAND` that user already had configured → broke existing setup
3. Using `simple-git` library that doesn't properly inherit environment → SSH agent socket not passed correctly

## Solution

1. **Use direct `spawn` instead of git libraries** - full environment inheritance
2. **Don't override `GIT_SSH_COMMAND` unless necessary** - preserve user's existing configuration
3. **Add `BatchMode=yes` by appending to existing `GIT_SSH_COMMAND`** if you need it
4. **Always set `stdio: 'inherit'`** - lets SSH interact with user if needed (agent forwarding, password prompts)
5. **Print the full command** - helps debugging when things go wrong

## Example

```typescript
import { spawn } from 'child_process';
import logger from './logger';

async function gitClone(repoUrl: string, localPath: string, cwd: string): Promise<GitResult> {
  return new Promise((resolve) => {
    // Print command for debugging
    const cmd = `git clone ${repoUrl} ${localPath}`;
    logger.info(`Running: ${cmd}`);
    
    // Full environment inheritance - preserves SSH_AUTH_SOCK from user's shell
    const env = { ...process.env };
    
    // Only add BatchMode=yes for SSH URLs
    // Append to existing GIT_SSH_COMMAND instead of overwriting
    if (repoUrl.startsWith('git@') || repoUrl.startsWith('ssh://')) {
      const existingCmd = env.GIT_SSH_COMMAND || 'ssh';
      env.GIT_SSH_COMMAND = `${existingCmd} -o BatchMode=yes`;
    }

    const child = spawn('git', ['clone', repoUrl, localPath], {
      stdio: 'inherit',  // Important: lets SSH auth work with agent
      shell: true,
      cwd,
      env,
    });

    child.on('close', (code: number) => {
      if (code === 0) {
        resolve({ success: true, data: undefined });
      } else {
        resolve({
          success: false,
          error: `git clone failed with exit code ${code}`
        });
      }
    });
  });
}
```

## Key Points

| Practice | Why |
|----------|-----|
| `env = { ...process.env }` | Preserves `SSH_AUTH_SOCK` which is how ssh-agent communicates |
| Don't override `GIT_SSH_COMMAND` | User may already have custom config for proxy/keys/hosts |
| `stdio: 'inherit'` | Lets SSH talk directly to terminal for interactive prompts if needed |
| Print full command | User can copy-paste and run it manually to debug |

## When to Use

- Any Node.js CLI tool that spawns git operations on SSH repositories
- Building developer tools that work with private git repositories
- When users report "it works when I run git manually but not from your tool"
- Non-interactive CLI tools that need to handle SSH authentication
