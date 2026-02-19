Use this file to provide workspace-specific custom instructions to Copilot. For more details, visit https://code.visualstudio.com/docs/copilot/copilot-customization#_use-a-githubcopilotinstructionsmd-file.

- [ ] Verify that the copilot-instructions.md file in the .github directory is created.

- [ ] Clarify Project Requirements
	- Ask for project type, language, and frameworks if not specified. Skip if already provided.

- [ ] Scaffold the Project
	- Ensure that the previous step has been marked as completed.
	- Call the project setup tool with the projectType parameter.
	- Run the scaffolding command to create project files and folders.
	- Use '.' as the working directory.
	- If no appropriate projectType is available, search documentation using available tools.
	- Otherwise, create the project structure manually using available file creation tools.

- [ ] Customize the Project
	- Verify that all previous steps have been completed successfully and you have marked the step as completed.
	- Develop a plan to modify the codebase according to user requirements.
	- Apply modifications using appropriate tools and user-provided references.
	- Skip this step for "Hello World" projects.

- [ ] Install Required Extensions
	- Only install extensions provided by the get_project_setup_info tool. Skip this step otherwise and mark it as completed.

- [ ] Compile the Project
	- Verify that all previous steps have been completed.
	- Install any missing dependencies.
	- Run diagnostics and resolve any issues.
	- Check for markdown files in the project folder for relevant instructions on how to do this.

- [ ] Create and Run Task
	- Verify that all previous steps have been completed.
	- Check https://code.visualstudio.com/docs/debugtest/tasks to determine if the project needs a task. If so, use create_and_run_task to create and launch a task based on package.json, README.md, and the project structure.
	- Skip this step otherwise.

- [ ] Launch the Project
	- Verify that all previous steps have been completed.
	- Prompt the user for debug mode and launch only if confirmed.

- [ ] Ensure Documentation is Complete
	- Verify that all previous steps have been completed.
	- Verify that README.md and the copilot-instructions.md file exist and contain current project information.
	- Remove any stale guidance from this file whenever requirements change.

## Execution Guidelines

### Progress Tracking
- If any tools are available to manage the above todo list, use them to track progress through this checklist.
- After completing each step, mark it complete and add a summary.
- Read the current todo list status before starting each new step.

### Communication Rules
- Avoid verbose explanations or printing full command outputs.
- If a step is skipped, state that briefly (for example, "No extensions needed").
- Do not explain the project structure unless asked.
- Keep explanations concise and focused.

### Development Rules
- Use '.' as the working directory unless the user specifies otherwise.
- Avoid adding media or external links unless explicitly requested.
- Use placeholders only with a note that they should be replaced.
- Use the VS Code API tool only for VS Code extension projects.
- Once the project is created, it is already opened in Visual Studio Code—do not suggest commands to open this project in Visual Studio again.
- If the project setup information has additional rules, follow them strictly.

### Folder Creation Rules
- Always use the current directory as the project root.
- When running terminal commands, use the '.' argument to ensure the current working directory is used.
- Do not create a new folder unless the user explicitly requests it (besides a .vscode folder for a tasks.json file).
- If any scaffolding commands mention that the folder name is not correct, let the user know to create a new folder with the correct name and reopen it in VS Code.

### Extension Installation Rules
- Only install extensions specified by the get_project_setup_info tool. Do not install any other extensions.

### Project Content Rules
- If the user has not specified project details, assume they want a "Hello World" project as a starting point.
- Avoid adding links of any type (URLs, files, folders, etc.) or integrations that are not explicitly required.
- Avoid generating images, videos, or any other media files unless explicitly requested.
- If you need to use any media assets as placeholders, let the user know that these should be replaced with the actual assets later.
- Ensure all generated components serve a clear purpose within the user's requested workflow.
- If a feature is assumed but not confirmed, prompt the user for clarification before including it.
- If you are working on a VS Code extension, use the VS Code API tool with a query to find relevant references and samples.

### Task Completion Rules
- The task is complete when:
  - The project is successfully scaffolded and compiled without errors.
  - copilot-instructions.md exists in the project.
  - README.md exists and is up to date.
  - The user is provided with clear instructions to debug or launch the project.

Before starting a new task in the above plan, update progress in the plan.

- Work through each checklist item systematically.
- Keep communication concise and focused.
- Follow development best practices.
