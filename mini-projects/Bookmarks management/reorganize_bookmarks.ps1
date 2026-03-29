param(
    [string]$InputPath = 'favorites_3_29_26.html',
    [string]$OutputPath = 'favorites_3_29_26_reorganized.html'
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'


function New-FolderNode {
    param(
        [Parameter(Mandatory)] [string]$Name,
        [string]$Attrs = ''
    )

    return [ordered]@{
        Type     = 'folder'
        Name     = $Name
        Attrs    = $Attrs
        Children = [System.Collections.ArrayList]::new()
    }
}

function New-LinkNode {
    param(
        [Parameter(Mandatory)] [string]$Href,
        [string]$Attrs = '',
        [string]$Title
    )

    return [ordered]@{
        Type  = 'link'
        Href  = $Href
        Attrs = $Attrs
        Title = $Title
    }
}

function Add-ChildNode {
    param(
        [Parameter(Mandatory)] [hashtable]$Parent,
        [Parameter(Mandatory)] [hashtable]$Child
    )

    [void]$Parent.Children.Add($Child)
}

function Clone-Node {
    param([Parameter(Mandatory)] [hashtable]$Node)

    if ($Node.Type -eq 'folder') {
        $clone = New-FolderNode -Name $Node.Name -Attrs $Node.Attrs
        foreach ($child in $Node.Children) {
            Add-ChildNode -Parent $clone -Child (Clone-Node -Node $child)
        }
        return $clone
    }

    return (New-LinkNode -Href $Node.Href -Attrs $Node.Attrs -Title $Node.Title)
}

function Get-ChildFolder {
    param(
        [Parameter(Mandatory)] [hashtable]$Folder,
        [Parameter(Mandatory)] [string]$Name
    )

    foreach ($child in $Folder.Children) {
        if ($child.Type -eq 'folder' -and $child.Name -eq $Name) {
            return $child
        }
    }

    return $null
}

function Ensure-FolderPath {
    param(
        [Parameter(Mandatory)] [hashtable]$Root,
        [Parameter(Mandatory)] [string[]]$Path
    )

    $current = $Root
    foreach ($segment in $Path) {
        $next = Get-ChildFolder -Folder $current -Name $segment
        if ($null -eq $next) {
            $next = New-FolderNode -Name $segment
            Add-ChildNode -Parent $current -Child $next
        }
        $current = $next
    }

    return $current
}

function Add-NodeAtPath {
    param(
        [Parameter(Mandatory)] [hashtable]$Root,
        [Parameter(Mandatory)] [string[]]$Path,
        [Parameter(Mandatory)] [hashtable]$Node
    )

    $target = Ensure-FolderPath -Root $Root -Path $Path
    Add-ChildNode -Parent $target -Child $Node
}

function Merge-ChildNodesIntoFolder {
    param(
        [Parameter(Mandatory)] [hashtable]$TargetFolder,
        [Parameter(Mandatory)] [hashtable]$SourceFolder
    )

    foreach ($child in $SourceFolder.Children) {
        Add-ChildNode -Parent $TargetFolder -Child (Clone-Node -Node $child)
    }
}

function Reorganize-RoleFolder {
    param([Parameter(Mandatory)] [hashtable]$RoleFolder)

    $newRole = New-FolderNode -Name 'Rol' -Attrs $RoleFolder.Attrs

    foreach ($child in $RoleFolder.Children) {
        if ($child.Type -eq 'link') {
            Add-NodeAtPath -Root $newRole -Path @('General') -Node (Clone-Node -Node $child)
            continue
        }

        $targetPath = switch ($child.Name) {
            'Generic Games' { @('General') }
            'D&amp;D' { @('D&amp;D') }
            'D&amp;D5E' { @('D&amp;D') }
            'Traveller campaign' { @('Traveller') }
            'Pathfinder' { @('Pathfinder') }
            'Achtung! Cthulhu' { @('Achtung! Cthulhu') }
            'Fantasy Grounds' { @('Herramientas', 'Fantasy Grounds') }
            'Star Wars' { @('Star Wars') }
            'Solo RPG' { @('Solo') }
            default { @('99 Pendiente') }
        }

        $targetFolder = Ensure-FolderPath -Root $newRole -Path $targetPath
        Merge-ChildNodesIntoFolder -TargetFolder $targetFolder -SourceFolder $child
    }

    return $newRole
}

function Reorganize-MiniaturesFolder {
    param(
        [Parameter(Mandatory)] [hashtable]$MiniaturesFolder,
        [hashtable]$ExternalAtzFolder
    )

    $newMiniatures = New-FolderNode -Name 'Miniaturas' -Attrs $MiniaturesFolder.Attrs

    foreach ($child in $MiniaturesFolder.Children) {
        if ($child.Type -eq 'link') {
            Add-NodeAtPath -Root $newMiniatures -Path @('99 Pendiente') -Node (Clone-Node -Node $child)
            continue
        }

        $newName = switch ($child.Name) {
            'WH-AoS' { 'Age of Sigmar' }
            'Other minis games' { 'Otros juegos' }
            default { $child.Name }
        }

        $clone = Clone-Node -Node $child
        $clone.Name = $newName
        Add-ChildNode -Parent $newMiniatures -Child $clone
    }

    if ($null -ne $ExternalAtzFolder) {
        $atzFolder = Get-ChildFolder -Folder $newMiniatures -Name 'ATZ'
        if ($null -eq $atzFolder) {
            $atzFolder = New-FolderNode -Name 'ATZ'
            Add-ChildNode -Parent $newMiniatures -Child $atzFolder
        }

        Merge-ChildNodesIntoFolder -TargetFolder $atzFolder -SourceFolder $ExternalAtzFolder
    }

    return $newMiniatures
}

function Get-DirectLinkTargetPath {
    param([Parameter(Mandatory)] [hashtable]$LinkNode)

    switch ($LinkNode.Title) {
        'The Movie Database (TMDB)' { return @('02 Entretenimiento Digital', 'PelÃ­culas y series', 'Referencias') }
        'The Machine 2013 1080p BRRiP x264 TheKing - YouTube' { return @('02 Entretenimiento Digital', 'PelÃ­culas y series') }
        'Google API Tutorial' { return @('03 Infra y TecnologÃ­a', 'General') }
        '300+ Mind Expanding Documentaries' { return @('04 Intereses Personales', 'Curiosidades') }
        'Filth Wizardry: Milk Jug Storm Trooper Helmet' { return @('01 Rol, Mesa y Miniaturas', 'Miniaturas', 'InspiraciÃ³n') }
        'bop.fm' { return @('04 Intereses Personales', 'MÃºsica') }
        'tunlr.net' { return @('99 Pendiente de clasificar') }
        'Android Device Manager' { return @('03 Infra y TecnologÃ­a', 'Android') }
        '8muses - Free Sex Comics And Adult Cartoons' { return @('02 Entretenimiento Digital', 'Libros y cÃ³mics') }
        'Center for UFO Studies' { return @('04 Intereses Personales', 'Curiosidades') }
        'Papercraft&#39;s library for xwing (cheapest proxies) | Star Wars: X-Wing Miniatures Game | BoardGameGeek' { return @('01 Rol, Mesa y Miniaturas', 'Miniaturas', 'X-Wing') }
        default { return @('99 Pendiente de clasificar') }
    }
}

function Reorganize-PersonalFolder {
    param([Parameter(Mandatory)] [hashtable]$PersonalFolder)

    $newPersonal = New-FolderNode -Name $PersonalFolder.Name -Attrs $PersonalFolder.Attrs
    $miniaturesFolder = $null
    $externalAtzFolder = $null

    foreach ($child in $PersonalFolder.Children) {
        if ($child.Type -eq 'link') {
            Add-NodeAtPath -Root $newPersonal -Path (Get-DirectLinkTargetPath -LinkNode $child) -Node (Clone-Node -Node $child)
            continue
        }

        switch ($child.Name) {
            'Rol' {
                Add-NodeAtPath -Root $newPersonal -Path @('01 Rol, Mesa y Miniaturas') -Node (Reorganize-RoleFolder -RoleFolder $child)
            }
            'Miniatures' {
                $miniaturesFolder = $child
            }
            'Wargames' {
                $clone = Clone-Node -Node $child
                $clone.Name = 'Juegos de guerra'
                Add-NodeAtPath -Root $newPersonal -Path @('01 Rol, Mesa y Miniaturas') -Node $clone
            }
            'ATZ blogs' {
                $externalAtzFolder = $child
            }
            'Resistance campaign' {
                $clone = Clone-Node -Node $child
                $clone.Name = 'CampaÃ±as'
                Add-NodeAtPath -Root $newPersonal -Path @('01 Rol, Mesa y Miniaturas', 'Juegos de guerra') -Node $clone
            }
            'Torrents' {
                $clone = Clone-Node -Node $child
                $clone.Name = 'Fuentes de descarga'
                Add-NodeAtPath -Root $newPersonal -Path @('02 Entretenimiento Digital') -Node $clone
            }
            'TV y series' {
                Add-NodeAtPath -Root $newPersonal -Path @('02 Entretenimiento Digital') -Node (Clone-Node -Node $child)
            }
            'VideoGames' {
                $clone = Clone-Node -Node $child
                $clone.Name = 'Videojuegos'
                Add-NodeAtPath -Root $newPersonal -Path @('02 Entretenimiento Digital') -Node $clone
            }
            'Libros' {
                $clone = Clone-Node -Node $child
                $clone.Name = 'Libros y cÃ³mics'
                Add-NodeAtPath -Root $newPersonal -Path @('02 Entretenimiento Digital') -Node $clone
            }
            'Magazines' {
                $clone = Clone-Node -Node $child
                $clone.Name = 'Revistas'
                Add-NodeAtPath -Root $newPersonal -Path @('02 Entretenimiento Digital') -Node $clone
            }
            'Web design' {
                $clone = Clone-Node -Node $child
                $clone.Name = 'DiseÃ±o y web'
                Add-NodeAtPath -Root $newPersonal -Path @('03 Infra y TecnologÃ­a') -Node $clone
            }
            'Web Hosting' {
                $clone = Clone-Node -Node $child
                $clone.Name = 'Hosting y servidores'
                Add-NodeAtPath -Root $newPersonal -Path @('03 Infra y TecnologÃ­a') -Node $clone
            }
            'IA' {
                Add-NodeAtPath -Root $newPersonal -Path @('03 Infra y TecnologÃ­a') -Node (Clone-Node -Node $child)
            }
            'Android' {
                Add-NodeAtPath -Root $newPersonal -Path @('03 Infra y TecnologÃ­a') -Node (Clone-Node -Node $child)
            }
            'Productivity' {
                $clone = Clone-Node -Node $child
                $clone.Name = 'Productividad'
                Add-NodeAtPath -Root $newPersonal -Path @('03 Infra y TecnologÃ­a') -Node $clone
            }
            'Inkscape' {
                Add-NodeAtPath -Root $newPersonal -Path @('03 Infra y TecnologÃ­a', 'DiseÃ±o y web') -Node (Clone-Node -Node $child)
            }
            'Computer' {
                $clone = Clone-Node -Node $child
                $clone.Name = 'General'
                Add-NodeAtPath -Root $newPersonal -Path @('03 Infra y TecnologÃ­a') -Node $clone
            }
            'Electronica' {
                $clone = Clone-Node -Node $child
                $clone.Name = 'ElectrÃ³nica'
                Add-NodeAtPath -Root $newPersonal -Path @('03 Infra y TecnologÃ­a') -Node $clone
            }
            'Curiosidades' {
                Add-NodeAtPath -Root $newPersonal -Path @('04 Intereses Personales') -Node (Clone-Node -Node $child)
            }
            'Science fiction' {
                $clone = Clone-Node -Node $child
                $clone.Name = 'Ciencia ficciÃ³n'
                Add-NodeAtPath -Root $newPersonal -Path @('04 Intereses Personales') -Node $clone
            }
            'Science' {
                $clone = Clone-Node -Node $child
                $clone.Name = 'Ciencia'
                Add-NodeAtPath -Root $newPersonal -Path @('04 Intereses Personales') -Node $clone
            }
            'Magick' {
                Add-NodeAtPath -Root $newPersonal -Path @('04 Intereses Personales') -Node (Clone-Node -Node $child)
            }
            'Aikido' {
                Add-NodeAtPath -Root $newPersonal -Path @('04 Intereses Personales') -Node (Clone-Node -Node $child)
            }
            'Viatge a Tokyo' {
                $clone = Clone-Node -Node $child
                $clone.Name = 'Tokyo'
                Add-NodeAtPath -Root $newPersonal -Path @('05 Viajes y Vida Personal', 'Viajes') -Node $clone
            }
            'Profesional' {
                Add-NodeAtPath -Root $newPersonal -Path @('06 Profesional y Proyectos') -Node (Clone-Node -Node $child)
            }
            default {
                Add-NodeAtPath -Root $newPersonal -Path @('99 Pendiente de clasificar') -Node (Clone-Node -Node $child)
            }
        }
    }

    if ($null -ne $miniaturesFolder) {
        Add-NodeAtPath -Root $newPersonal -Path @('01 Rol, Mesa y Miniaturas') -Node (Reorganize-MiniaturesFolder -MiniaturesFolder $miniaturesFolder -ExternalAtzFolder $externalAtzFolder)
    } elseif ($null -ne $externalAtzFolder) {
        $miniaturesRoot = Ensure-FolderPath -Root $newPersonal -Path @('01 Rol, Mesa y Miniaturas', 'Miniaturas', 'ATZ')
        Merge-ChildNodesIntoFolder -TargetFolder $miniaturesRoot -SourceFolder $externalAtzFolder
    }

    return $newPersonal
}

function Write-Node {
    param(
        [Parameter(Mandatory)] [hashtable]$Node,
        [Parameter(Mandatory)] [System.Text.StringBuilder]$Builder,
        [Parameter(Mandatory)] [string]$Indent
    )

    if ($Node.Type -eq 'folder') {
        $folderAttrs = $Node.Attrs.Trim()
        $folderAttrText = if ([string]::IsNullOrWhiteSpace($folderAttrs)) { '' } else { " $folderAttrs" }
        [void]$Builder.AppendLine("$Indent<DT><H3$folderAttrText>$($Node.Name)</H3>")
        [void]$Builder.AppendLine("$Indent<DL><p>")
        foreach ($child in $Node.Children) {
            Write-Node -Node $child -Builder $Builder -Indent ($Indent + '    ')
        }
        [void]$Builder.AppendLine("$Indent</DL><p>")
        return
    }

    $linkAttrs = $Node.Attrs.Trim()
    $linkAttrText = if ([string]::IsNullOrWhiteSpace($linkAttrs)) { '' } else { " $linkAttrs" }
    [void]$Builder.AppendLine("$Indent<DT><A HREF=""$($Node.Href)""$linkAttrText>$($Node.Title)</A>")
}

$content = Get-Content -Path $InputPath -Raw
$tokenPattern = '<DT><H3(?<h3attrs>[^>]*)>(?<folder>.*?)</H3>|<DT><A (?<aattrs1>[^>]*?)HREF="(?<href>[^"]+)"(?<aattrs2>[^>]*)>(?<title>.*?)</A>|<DL><p>|</DL><p>'
$tokens = [regex]::Matches($content, $tokenPattern, 'IgnoreCase')

$root = New-FolderNode -Name '__ROOT__'
$stack = [System.Collections.Stack]::new()
$stack.Push($root)
$pendingFolder = $null

foreach ($match in $tokens) {
    $value = $match.Value

    if ($match.Groups['folder'].Success) {
        $pendingFolder = New-FolderNode -Name $match.Groups['folder'].Value -Attrs $match.Groups['h3attrs'].Value
        continue
    }

    if ($value -eq '<DL><p>') {
        if ($null -ne $pendingFolder) {
            Add-ChildNode -Parent $stack.Peek() -Child $pendingFolder
            $stack.Push($pendingFolder)
            $pendingFolder = $null
        }
        continue
    }

    if ($value -eq '</DL><p>') {
        if ($stack.Count -gt 1) {
            [void]$stack.Pop()
        }
        continue
    }

    if ($match.Groups['href'].Success) {
        $attrs = ($match.Groups['aattrs1'].Value + ' ' + $match.Groups['aattrs2'].Value).Trim()
        $link = New-LinkNode -Href $match.Groups['href'].Value -Attrs $attrs -Title $match.Groups['title'].Value
        Add-ChildNode -Parent $stack.Peek() -Child $link
    }
}

$newRoot = New-FolderNode -Name '__ROOT__'

foreach ($topNode in $root.Children) {
    if ($topNode.Type -eq 'folder' -and $topNode.Name -eq 'Favorites bar') {
        $favoritesClone = Clone-Node -Node $topNode
        $newFavorites = New-FolderNode -Name $favoritesClone.Name -Attrs $favoritesClone.Attrs

        foreach ($favoriteChild in $favoritesClone.Children) {
            if ($favoriteChild.Type -eq 'folder' -and $favoriteChild.Name -eq 'Personal') {
                Add-ChildNode -Parent $newFavorites -Child (Reorganize-PersonalFolder -PersonalFolder $favoriteChild)
            } else {
                Add-ChildNode -Parent $newFavorites -Child $favoriteChild
            }
        }

        Add-ChildNode -Parent $newRoot -Child $newFavorites
    } else {
        Add-ChildNode -Parent $newRoot -Child (Clone-Node -Node $topNode)
    }
}

$builder = [System.Text.StringBuilder]::new()
[void]$builder.AppendLine('<!DOCTYPE NETSCAPE-Bookmark-file-1>')
[void]$builder.AppendLine('<!-- This is an automatically generated file.')
[void]$builder.AppendLine('     It will be read and overwritten.')
[void]$builder.AppendLine('     DO NOT EDIT! -->')
[void]$builder.AppendLine('<META HTTP-EQUIV="Content-Type" CONTENT="text/html; charset=UTF-8">')
[void]$builder.AppendLine('<TITLE>Bookmarks</TITLE>')
[void]$builder.AppendLine('<H1>Bookmarks</H1>')
[void]$builder.AppendLine('<DL><p>')

foreach ($child in $newRoot.Children) {
    Write-Node -Node $child -Builder $builder -Indent '    '
}

[void]$builder.AppendLine('</DL><p>')

Set-Content -Path $OutputPath -Value $builder.ToString() -Encoding UTF8
Write-Output "Generated $OutputPath"


