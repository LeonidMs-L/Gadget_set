window.MathJax = {
  tex: {
    packages: {'[-]': ['autoload', 'require', 'html']},
    formatError: (_jax, error) => { throw error; }
  },
  svg: {fontCache: 'none'},
  startup: {typeset: false}
};
