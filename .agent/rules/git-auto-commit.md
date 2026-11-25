# Auto-Commit e Push no GitHub

Sempre que concluir uma tarefa com alterações válidas neste projeto e após rodar as verificações necessárias (ex.: `python manage.py check` e testes aplicáveis):

1. Execute `git add` apenas nos arquivos alterados da entrega (sem incluir arquivos temporários ou sensíveis como `.env`).
2. Faça o `git commit` com mensagem descritiva no padrão Conventional Commits (ex.: `feat(...)`, `fix(...)`, `refactor(...)`).
3. Execute `git push` para a branch atual (`main`) no remote `origin` (`https://github.com/Ricardoqs20/CRM.git`).
4. Informe o hash do commit e os arquivos enviados na resposta final. Caso o usuário peça para reverter depois, utilize `git revert` (ou o ajuste solicitado) e envie o novo commit.
