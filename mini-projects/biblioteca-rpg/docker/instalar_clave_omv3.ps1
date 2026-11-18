# Copia la clave pública de este PC a OMV3. Pide la contraseña de root UNA vez;
# después, ssh omv3 entra sin contraseña. Si la clave ya estaba, no la duplica.
$clave = (Get-Content "$env:USERPROFILE\.ssh\id_ed25519_omv3.pub" -Raw).Trim()
$remoto = 'k=$(cat); umask 077; mkdir -p ~/.ssh; touch ~/.ssh/authorized_keys; ' +
          'grep -qxF "$k" ~/.ssh/authorized_keys || echo "$k" >> ~/.ssh/authorized_keys; echo CLAVE_INSTALADA'
$clave | ssh -o PreferredAuthentications=password,keyboard-interactive root@openmediavault3.local $remoto
