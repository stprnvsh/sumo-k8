# Use existing VPC or create new
data "aws_vpc" "existing" {
  count = var.create_vpc ? 0 : 1
  id    = var.vpc_id
}

resource "aws_vpc" "this" {
  count            = var.create_vpc ? 1 : 0
  cidr_block       = var.vpc_cidr
  enable_dns_hostnames = true
  enable_dns_support   = true
  
  tags = merge(
    var.tags,
    {
      Name = "${var.cluster_name}-vpc"
      "karpenter.sh/discovery" = var.cluster_name
    }
  )
}

locals {
  vpc_id = var.create_vpc ? aws_vpc.this[0].id : var.vpc_id
}

# Public subnets
data "aws_subnet" "existing_public" {
  count = var.create_vpc ? 0 : length(var.public_subnet_ids)
  id    = var.public_subnet_ids[count.index]
}

resource "aws_subnet" "public" {
  count             = var.create_vpc ? length(var.availability_zones) : 0
  vpc_id            = local.vpc_id
  cidr_block        = cidrsubnet(var.vpc_cidr, 8, count.index)
  availability_zone = var.availability_zones[count.index]
  map_public_ip_on_launch = true
  
  tags = merge(
    var.tags,
    {
      Name = "${var.cluster_name}-public-${count.index + 1}"
      "kubernetes.io/role/elb" = "1"
      "karpenter.sh/discovery" = var.cluster_name
    }
  )
}

# Private subnets
data "aws_subnet" "existing_private" {
  count = var.create_vpc ? 0 : length(var.private_subnet_ids)
  id    = var.private_subnet_ids[count.index]
}

resource "aws_subnet" "private" {
  count             = var.create_vpc ? length(var.availability_zones) : 0
  vpc_id            = local.vpc_id
  cidr_block        = cidrsubnet(var.vpc_cidr, 8, count.index + 10)
  availability_zone = var.availability_zones[count.index]
  
  tags = merge(
    var.tags,
    {
      Name = "${var.cluster_name}-private-${count.index + 1}"
      "kubernetes.io/role/internal-elb" = "1"
      "karpenter.sh/discovery" = var.cluster_name
    }
  )
}

locals {
  public_subnet_ids  = var.create_vpc ? aws_subnet.public[*].id : var.public_subnet_ids
  private_subnet_ids = var.create_vpc ? aws_subnet.private[*].id : var.private_subnet_ids
}

# Internet Gateway (only if creating VPC)
resource "aws_internet_gateway" "this" {
  count  = var.create_vpc ? 1 : 0
  vpc_id = local.vpc_id
  
  tags = merge(var.tags, { Name = "${var.cluster_name}-igw" })
}

# NAT Gateway (only if creating VPC)
resource "aws_eip" "nat" {
  count  = var.create_vpc ? 1 : 0
  domain = "vpc"
  tags   = merge(var.tags, { Name = "${var.cluster_name}-nat-eip" })
}

resource "aws_nat_gateway" "this" {
  count         = var.create_vpc ? 1 : 0
  allocation_id = aws_eip.nat[0].id
  subnet_id     = local.public_subnet_ids[0]
  
  tags = merge(var.tags, { Name = "${var.cluster_name}-nat" })
}

# Route tables
resource "aws_route_table" "public" {
  count  = var.create_vpc ? 1 : 0
  vpc_id = local.vpc_id
  
  route {
    cidr_block = "0.0.0.0/0"
    gateway_id = aws_internet_gateway.this[0].id
  }
  
  tags = merge(var.tags, { Name = "${var.cluster_name}-public-rt" })
}

resource "aws_route_table_association" "public" {
  count          = var.create_vpc ? length(aws_subnet.public) : 0
  subnet_id      = aws_subnet.public[count.index].id
  route_table_id = aws_route_table.public[0].id
}

resource "aws_route_table" "private" {
  count  = var.create_vpc ? 1 : 0
  vpc_id = local.vpc_id
  
  route {
    cidr_block     = "0.0.0.0/0"
    nat_gateway_id = aws_nat_gateway.this[0].id
  }
  
  tags = merge(var.tags, { Name = "${var.cluster_name}-private-rt" })
}

resource "aws_route_table_association" "private" {
  count          = var.create_vpc ? length(aws_subnet.private) : 0
  subnet_id      = aws_subnet.private[count.index].id
  route_table_id = aws_route_table.private[0].id
}

# Security Group
data "aws_security_group" "existing" {
  count = var.create_vpc ? 0 : 1
  vpc_id = local.vpc_id
  filter {
    name   = "tag:karpenter.sh/discovery"
    values = [var.cluster_name]
  }
}

resource "aws_security_group" "cluster" {
  count       = var.create_vpc ? 1 : 0
  name_prefix = "${var.cluster_name}-"
  vpc_id      = local.vpc_id
  description = "Security group for ${var.cluster_name}"
  
  ingress {
    from_port = 443
    to_port   = 443
    protocol  = "tcp"
    cidr_blocks = ["0.0.0.0/0"]
  }
  
  egress {
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }
  
  tags = merge(
    var.tags,
    {
      Name = "${var.cluster_name}-sg"
      "karpenter.sh/discovery" = var.cluster_name
    }
  )
}

locals {
  security_group_id = var.create_vpc ? aws_security_group.cluster[0].id : data.aws_security_group.existing[0].id
}

