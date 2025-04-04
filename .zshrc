# Enable Powerlevel10k instant prompt. Should stay close to the top of ~/.zshrc.
# Initialization code that may require console input (password prompts, [y/n]
# confirmations, etc.) must go above this block; everything else may go below.
if [[ -r "${XDG_CACHE_HOME:-$HOME/.cache}/p10k-instant-prompt-${(%):-%n}.zsh" ]]; then
  source "${XDG_CACHE_HOME:-$HOME/.cache}/p10k-instant-prompt-${(%):-%n}.zsh"
fi

# If you come from bash you might have to change your $PATH.
# export PATH=$HOME/bin:/usr/local/bin:$PATH

# Path to your oh-my-zsh installation.
export ZSH="$HOME/.oh-my-zsh"

# Set name of the theme to load --- if set to "random", it will
# load a random theme each time oh-my-zsh is loaded, in which case,
# to know which specific one was loaded, run: echo $RANDOM_THEME
# See https://github.com/ohmyzsh/ohmyzsh/wiki/Themes
ZSH_THEME="robbyrussell"

# Set list of themes to pick from when loading at random
# Setting this variable when ZSH_THEME=random will cause zsh to load
# a theme from this variable instead of looking in $ZSH/themes/
# If set to an empty array, this variable will have no effect.
# ZSH_THEME_RANDOM_CANDIDATES=( "robbyrussell" "agnoster" )

# Uncomment the following line to use case-sensitive completion.
# CASE_SENSITIVE="true"

# Uncomment the following line to use hyphen-insensitive completion.
# Case-sensitive completion must be off. _ and - will be interchangeable.
# HYPHEN_INSENSITIVE="true"

# Uncomment one of the following lines to change the auto-update behavior
# zstyle ':omz:update' mode disabled  # disable automatic updates
# zstyle ':omz:update' mode auto      # update automatically without asking
# zstyle ':omz:update' mode reminder  # just remind me to update when it's time

# Uncomment the following line to change how often to auto-update (in days).
# zstyle ':omz:update' frequency 13

# Uncomment the following line if pasting URLs and other text is messed up.
# DISABLE_MAGIC_FUNCTIONS="true"

# Uncomment the following line to disable colors in ls.
# DISABLE_LS_COLORS="true"

# Uncomment the following line to disable auto-setting terminal title.
# DISABLE_AUTO_TITLE="true"

# Uncomment the following line to enable command auto-correction.
# ENABLE_CORRECTION="true"

# Uncomment the following line to display red dots whilst waiting for completion.
# You can also set it to another string to have that shown instead of the default red dots.
# e.g. COMPLETION_WAITING_DOTS="%F{yellow}waiting...%f"
# Caution: this setting can cause issues with multiline prompts in zsh < 5.7.1 (see #5765)
# COMPLETION_WAITING_DOTS="true"

# Uncomment the following line if you want to disable marking untracked files
# under VCS as dirty. This makes repository status check for large repositories
# much, much faster.
# DISABLE_UNTRACKED_FILES_DIRTY="true"

# Uncomment the following line if you want to change the command execution time
# stamp shown in the history command output.
# You can set one of the optional three formats:
# "mm/dd/yyyy"|"dd.mm.yyyy"|"yyyy-mm-dd"
# or set a custom format using the strftime function format specifications,
# see 'man strftime' for details.
# HIST_STAMPS="mm/dd/yyyy"

# Would you like to use another custom folder than $ZSH/custom?
# ZSH_CUSTOM=/path/to/new-custom-folder

# Which plugins would you like to load?
# Standard plugins can be found in $ZSH/plugins/
# Custom plugins may be added to $ZSH_CUSTOM/plugins/
# Example format: plugins=(rails git textmate ruby lighthouse)
# Add wisely, as too many plugins slow down shell startup.
plugins=(git)

if [ -f "$ZSH/oh-my-zsh.sh" ]; then
    source $ZSH/oh-my-zsh.sh
fi

# User configuration

# export MANPATH="/usr/local/man:$MANPATH"

# You may need to manually set your language environment
# export LANG=en_US.UTF-8

# Preferred editor for local and remote sessions
# if [[ -n $SSH_CONNECTION ]]; then
#   export EDITOR='vim'
# else
#   export EDITOR='mvim'
# fi

# Compilation flags
# export ARCHFLAGS="-arch x86_64"

# Set personal aliases, overriding those provided by oh-my-zsh libs,
# plugins, and themes. Aliases can be placed here, though oh-my-zsh
# users are encouraged to define aliases within the ZSH_CUSTOM folder.
# For a full list of active aliases, run `alias`.
#
# Example aliases
# alias zshconfig="mate ~/.zshrc"
# alias ohmyzsh="mate ~/.oh-my-zsh"
### Added by Zinit's installer
# Determine OS type

case "$(uname -s)" in
    Linux*)     os=Linux;;
    Darwin*)    os=Mac;;
    *)          os=Unknown;;
esac

if [ "$os" = "Mac" ]; then    
    eval "$(/opt/homebrew/bin/brew shellenv)"
fi

if [[ ! -f $HOME/.local/share/zinit/zinit.git/zinit.zsh ]]; then
    print -P "%F{33} %F{220}Installing %F{33}ZDHARMA-CONTINUUM%F{220} Initiative Plugin Manager (%F{33}zdharma-continuum/zinit%F{220})…%f"
    command mkdir -p "$HOME/.local/share/zinit" && command chmod g-rwX "$HOME/.local/share/zinit"
    command git clone https://github.com/zdharma-continuum/zinit "$HOME/.local/share/zinit/zinit.git" && \
        print -P "%F{33} %F{34}Installation successful.%f%b" || \
        print -P "%F{160} The clone has failed.%f%b"
fi

source "$HOME/.local/share/zinit/zinit.git/zinit.zsh"
autoload -Uz _zinit
(( ${+_comps} )) && _comps[zinit]=_zinit

# Load a few important annexes, without Turbo
# (this is currently required for annexes)
zinit light-mode for \
    zdharma-continuum/zinit-annex-as-monitor \
    zdharma-continuum/zinit-annex-bin-gem-node \
    zdharma-continuum/zinit-annex-patch-dl \
    zdharma-continuum/zinit-annex-rust

### End of Zinit's installer chunk

# zinit plugins
zinit light zsh-users/zsh-autosuggestions
zinit light zdharma-continuum/fast-syntax-highlighting
zinit light jeffreytse/zsh-vi-mode
zinit light skywind3000/z.lua
# p10k
zinit ice depth=1; zinit light romkatv/powerlevel10k
# 
zinit ice lucid wait'0'
zinit light joshskidmore/zsh-fzf-history-search

# 解决 zsh-vi-mode 中 history 上下键冲突
zvm_bindkey vicmd '^[[A' history-search-backward
zvm_bindkey viins '^[[A' history-search-backward
zvm_bindkey vicmd '^[[B' history-search-forward
zvm_bindkey viins '^[[B' history-search-forward

# >>> NVM >>>
export NVM_DIR="$HOME/.nvm"
[ -s "$NVM_DIR/nvm.sh" ] && \. "$NVM_DIR/nvm.sh" # This loads nvm
# <<< NVM

# 将 ~/.cargo/bin 加入到 PATH
export PATH="$HOME/.cargo/bin:$PATH"

# >>> My Lias >>>
alias python="python3"
if command -v exa &> /dev/null; then
    alias ls="exa"
fi
if command -v eza &> /dev/null; then
    alias ls="eza"
fi
if command -v rg &> /dev/null; then
    alias grep="rg"
fi
if command -v nvim &> /dev/null; then
    alias vim="nvim"
fi
# <<< Alias

# To customize prompt, run `p10k configure` or edit ~/.p10k.zsh.
[[ ! -f ~/.p10k.zsh ]] || source ~/.p10k.zsh

# Conda initialize block


# Initialize Conda for Mac
if [ "$os" = "Mac" ]; then
    __conda_setup="$('/opt/homebrew/Caskroom/miniconda/base/bin/conda' 'shell.zsh' 'hook' 2> /dev/null)"
    if [ $? -eq 0 ]; then
        eval "$__conda_setup"
    else
        if [ -f "/opt/homebrew/Caskroom/miniconda/base/etc/profile.d/conda.sh" ]; then
            . "/opt/homebrew/Caskroom/miniconda/base/etc/profile.d/conda.sh"
        else
            export PATH="/opt/homebrew/Caskroom/miniconda/base/bin:$PATH"
        fi
    fi
    export GOPATH="$HOME/go"
    export GOROOT="/opt/homebrew/opt/go/libexec"
    export PATH=$PATH:$GOROOT/bin:$GOPATH/bin
    if [ -d "$HOME/go/bin" ] ; then
        export PATH="$HOME/go/bin:$PATH"
    fi
fi

# Initialize Conda for Linux
if [ "$os" = "Linux" ]; then
    __conda_setup="$("$HOME/miniconda3/bin/conda" 'shell.zsh' 'hook' 2> /dev/null)"
    if [ $? -eq 0 ]; then
        eval "$__conda_setup"
    else
        if [ -f "$HOME/miniconda3/etc/profile.d/conda.sh" ]; then
            . "$HOME/miniconda3/etc/profile.d/conda.sh"
        else
            export PATH="$HOME/miniconda3/bin:$PATH"
        fi
    fi
fi


unset __conda_setup
# deno
export DENO_INSTALL="$HOME/.deno"
export PATH="$DENO_INSTALL/bin:$PATH"

#neovim 
export PATH="$HOME/.local/nvim/bin:${PATH}"


load-nvmrc() {
  local nvmrc_path="$PWD/.nvmrc"

  # 检查 .nvmrc 文件是否存在并且不为空
  if [[ -f "$nvmrc_path" && -s "$nvmrc_path" ]]; then
    local nvm_version=$(<"$nvmrc_path")

    # 检查当前使用的 Node 版本是否与 .nvmrc 文件中指定的版本相同
    if [[ "$(nvm current)" != "$nvm_version" ]]; then
      nvm use
    fi
  fi
}
add-zsh-hook chpwd load-nvmrc
load-nvmrc

autoload -U add-zsh-hook
conda_auto_activate() {
    if [ -e ".condaenv" ]; then
        ENV_NAME=$(cat .condaenv)
        conda activate "$ENV_NAME"
    fi
}
add-zsh-hook chpwd conda_auto_activate 
conda_auto_activate

add_to_pythonpath() {
    # 获取当前目录
    local dir="$PWD"
    # 检查PYTHONPATH是否已经包含当前目录
    if [[ ":$PYTHONPATH:" != *":$dir:"* ]]; then
        # 如果不包含，将其添加到PYTHONPATH
        export PYTHONPATH="${PYTHONPATH:+$PYTHONPATH:}$dir"
        echo "Added $dir to PYTHONPATH."
    else
        echo "$dir is already in PYTHONPATH."
    fi
}
alias addpy='add_to_pythonpath'

ssh() {
    # 如果是在 tmux 里面我们将机器的名字作为当前window的名字
if [[ -n $TMUX ]]; then
    echo "in tmux"
    tmux rename-window "$(basename "$1")"
  fi
  if [[ $1 == gpu-* ]]; then
    echo "connecting to gpu using tssh"
    tssh "${1#ssh-}" "${@:2}"
  else
    command ssh "$@"
  fi

}

compctl -K _ssh ssh

# go bin path
export PATH="$PATH:$HOME/go/bin"

# 设置一些 myuid 和 mygid
export MY_UID=$(id -u)
export MY_GID=$(id -g)
export PATH="/opt/homebrew/opt/curl/bin:$PATH"

# 关闭 curl 的ipv6
alias curl="curl -4"

# .local bin
export PATH="$HOME/.local/bin:$PATH"

# ftf 检查 $HOME/.fzf.zsh 文件是否存在，如果存在则加载
if [ -f "$HOME/.fzf.zsh" ]; then
    source "$HOME/.fzf.zsh"
fi


# 检查本地是否有 .local.zsh.rc 文件，如果有则加载
if [ -f "$HOME/.local.zsh.rc" ]; then
    source "$HOME/.local.zsh.rc"
fi

# 如果安装了 fzf，我们激活它，fzf 是用来做模糊搜索的
if command -v fzf >/dev/null 2>&1; then
    source <(fzf --zsh)
    # 你可以在这里添加更多 fzf 的配置 开启 preview 功能
    export FZF_DEFAULT_OPTS='--height 90% --layout=reverse --border --preview="bat --style=numbers --color=always {}" --preview-window=right:60%:wrap'
fi

# pnpm
export PNPM_HOME="/Users/zhiwen.wang/Library/pnpm"
case ":$PATH:" in
  *":$PNPM_HOME:"*) ;;
  *) export PATH="$PNPM_HOME:$PATH" ;;
esac
# pnpm end

# Added by Windsurf
export PATH="/Users/zhiwen.wang/.codeium/windsurf/bin:$PATH"

export HOSTNAME=$(hostname)
